#!/usr/bin/env python3
"""Run the installed Lean Preview demo through public HTTP surfaces only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

EXPECTED_STEP_IDS = (
    "doctor_ready",
    "submit_governed_edict",
    "observe_decision_required",
    "resolve_decision_with_reason",
    "observe_completed_run",
    "verify_evidence_bundle",
    "propose_skill_candidate",
    "evaluate_candidate_gate",
    "start_skill_canary",
    "submit_canary_eligible_run",
    "verify_real_candidate_overlay",
    "rollback_candidate",
    "verify_new_run_uses_champion",
)

_DIGEST_LENGTH = 64
_ZERO_DIGEST = "0" * _DIGEST_LENGTH
_TERMINAL_FAILURES = {"cancelled", "canceled", "failed", "rejected", "error"}
_REQUIRED_GATE_NAMES = (
    "schema",
    "security",
    "regression",
    "sample",
    "evidence",
    "budget",
    "rollback",
    "human_veto",
)


class DemoRunError(RuntimeError):
    """The batch failed and its partial evidence was retained."""


class HttpResponse:
    def __init__(self, status: int, headers: Mapping[str, str], body: bytes) -> None:
        self.status = status
        self.headers = {str(key).lower(): str(value) for key, value in headers.items()}
        self.body = body


Transport = Callable[[str, str, dict[str, str], object | None, float], HttpResponse]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_timestamp(clock: Callable[[], datetime]) -> str:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _default_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: object | None,
    timeout: float,
) -> HttpResponse:
    encoded = None if body is None else _canonical_bytes(body)
    request = urllib.request.Request(url, data=encoded, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return HttpResponse(response.status, dict(response.headers.items()), response.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, dict(exc.headers.items()), exc.read())


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-blank")
    return value


def _integer(value: object, label: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return value


def _number(value: object, label: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return result


def _digest(value: object, label: str) -> str:
    text = _text(value, label)
    if len(text) != _DIGEST_LENGTH or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _commit(value: object) -> str:
    text = _text(value, "source_commit")
    if len(text) not in {40, 64} or any(character not in "0123456789abcdef" for character in text):
        raise ValueError("source_commit must be a full lowercase commit digest")
    return text


def _json_body(response: HttpResponse) -> dict[str, object]:
    try:
        return _mapping(json.loads(response.body), "HTTP response")
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("HTTP response was not a valid JSON object") from exc


def _response_correlation(response: HttpResponse, payload: dict[str, object]) -> str:
    header = response.headers.get("x-correlation-id")
    body = payload.get("correlation_id")
    body_value = body if isinstance(body, str) and body else None
    if header is not None and body_value is not None and header != body_value:
        raise ValueError("response correlation identity mismatch")
    correlation = header or body_value
    if correlation is None:
        raise ValueError("public response omitted its correlation identity")
    return correlation


class _StepTrace:
    def __init__(
        self,
        *,
        step_id: str,
        base_url: str,
        token: str,
        timeout: float,
        transport: Transport,
    ) -> None:
        self.step_id = step_id
        self.base_url = base_url
        self.token = token
        self.timeout = timeout
        self.transport = transport
        self.requests: list[dict[str, object]] = []
        self.response_hashes: list[str] = []
        self.correlation_ids: list[str] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        body: object | None = None,
        headers: Mapping[str, str] | None = None,
        accepted_statuses: tuple[int, ...] = (200,),
    ) -> dict[str, object]:
        method = method.upper()
        url = f"{self.base_url}{path}"
        request_headers = {"Accept": "application/json"}
        if path.startswith("/api/"):
            request_headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        self.requests.append(
            {
                "method": method,
                "path": path,
                "body_sha256": None if body is None else _canonical_hash(body),
            }
        )
        response = self.transport(method, url, request_headers, body, self.timeout)
        if response.status not in accepted_statuses:
            raise ValueError(f"public HTTP request returned status {response.status}")
        payload = _json_body(response)
        self.response_hashes.append(hashlib.sha256(response.body).hexdigest())
        self.correlation_ids.append(_response_correlation(response, payload))
        return payload

    def artifact(self, observed: object) -> dict[str, object]:
        return {
            "schema_version": 1,
            "step_id": self.step_id,
            "requests": self.requests,
            "correlation_ids": self.correlation_ids,
            "response_hashes": self.response_hashes,
            "observed": observed,
        }


def _data(payload: dict[str, object], label: str = "response data") -> dict[str, object]:
    return _mapping(payload.get("data"), label)


def _poll(
    trace: _StepTrace,
    *,
    path: str,
    ready: Callable[[dict[str, object]], object | None],
    max_attempts: int,
    interval: float,
    sleeper: Callable[[float], None],
) -> object:
    for attempt in range(max_attempts):
        payload = trace.request("GET", path)
        result = ready(payload)
        if result is not None:
            return result
        if attempt + 1 < max_attempts:
            sleeper(interval)
    raise TimeoutError(f"bounded polling exhausted after {max_attempts} attempts")


def _closed_content_hash(bundle: dict[str, object]) -> str:
    payload = dict(bundle)
    payload.pop("content_hash", None)
    return _canonical_hash(payload)


def _promotion_journal_id(principal_id: str, idempotency_key: str) -> str:
    identity = _canonical_hash({"principal_id": principal_id, "idempotency_key": idempotency_key})
    command_key = f"promotion:{identity}"
    return hashlib.sha256(f"{command_key}\0completed".encode()).hexdigest()


def _request_binding(action: str, body: dict[str, object]) -> dict[str, object]:
    binding = {
        "action": action,
        "expected_version": body["expected_version"],
        "idempotency_key": body["idempotency_key"],
        "decision_request_id": body["decision_request_id"],
        "body_sha256": _canonical_hash(body),
    }
    if action == "start_canary":
        binding["allocation_basis_points"] = body["allocation_basis_points"]
        binding["allocation_seed_id"] = body["allocation_seed_id"]
    return binding


def _artifact_path(artifact_root: Path, index: int, step_id: str) -> Path:
    return artifact_root / f"{index:02d}-{step_id}.json"


def _write_step(
    artifact_root: Path,
    *,
    index: int,
    step_id: str,
    status: str,
    started_at: str,
    completed_at: str,
    trace: _StepTrace,
    observed: object,
) -> dict[str, object]:
    artifact = trace.artifact(observed)
    path = _artifact_path(artifact_root, index, step_id)
    path.write_bytes(_canonical_bytes(artifact))
    return {
        "step_id": step_id,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "evidence_hashes": [_file_hash(path)],
        "observed_state_hash": _canonical_hash(observed),
    }


def _blocked_step(step_id: str, timestamp: str) -> dict[str, object]:
    return {
        "step_id": step_id,
        "status": "blocked",
        "started_at": timestamp,
        "completed_at": timestamp,
        "evidence_hashes": [],
        "observed_state_hash": _canonical_hash({"reason": "prior_step_failed"}),
    }


def _write_report(path: Path, state: dict[str, object], steps: list[dict[str, object]]) -> None:
    report = {
        "schema_version": 1,
        "batch_id": state["batch_id"],
        "source_commit": state["source_commit"],
        "wheel_sha256": state["wheel_sha256"],
        "environment_fingerprint": state["environment_fingerprint"],
        "fixture": state["fixture"],
        "steps": steps,
        "evidence_bundle_id": state.get("evidence_bundle_id", "unavailable"),
        "evidence_bundle_hash": state.get("evidence_bundle_hash", _ZERO_DIGEST),
        "candidate_id": state.get("candidate_id", "unavailable"),
        "gate_hash": state.get("gate_hash", _ZERO_DIGEST),
        "assignment_id": state.get("assignment_id", "unavailable"),
        "rollback_receipt_hash": state.get("rollback_receipt_hash", _ZERO_DIGEST),
        "external_pending": state["external_pending"],
    }
    report["content_hash"] = _canonical_hash(report)
    path.write_bytes(_canonical_bytes(report))


def run_demo(
    *,
    base_url: str,
    scenario_path: Path,
    batch_id: str,
    output_root: Path,
    transport: Transport = _default_transport,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleeper: Callable[[float], None] = time.sleep,
    environ: Mapping[str, str] = os.environ,
) -> Path:
    """Execute the canonical batch and return its immutable report path."""

    parsed_url = urllib.parse.urlsplit(base_url.rstrip("/"))
    if parsed_url.scheme != "http" or parsed_url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise DemoRunError("base URL must be an explicit loopback HTTP endpoint")
    clean_base_url = base_url.rstrip("/")
    batch_id = _text(batch_id, "batch_id")
    if "/" in batch_id or "\\" in batch_id or batch_id in {".", ".."}:
        raise DemoRunError("batch_id must be one path-safe component")
    batch_root = output_root / batch_id
    if batch_root.exists():
        raise DemoRunError(f"batch already exists and is retained: {batch_id}")

    try:
        scenario = _mapping(json.loads(scenario_path.read_bytes()), "scenario")
        if scenario.get("schema_version") != 1:
            raise ValueError("scenario schema_version must be 1")
        provenance = _mapping(scenario.get("provenance"), "provenance")
        polling = _mapping(scenario.get("polling"), "polling")
        edict = _mapping(scenario.get("edict"), "edict")
        decision = _mapping(scenario.get("decision"), "decision")
        skill = _mapping(scenario.get("skill"), "skill")
        canary = _mapping(scenario.get("canary"), "canary")
        rollback = _mapping(scenario.get("rollback"), "rollback")
        token_env = _text(scenario.get("auth_token_env"), "auth_token_env")
        token = _text(environ.get(token_env), token_env)
        source_commit = _commit(
            environ.get("TIANSHU_LEAN_SOURCE_COMMIT", provenance.get("source_commit"))
        )
        wheel_sha256 = _digest(
            environ.get("TIANSHU_LEAN_WHEEL_SHA256", provenance.get("wheel_sha256")),
            "wheel_sha256",
        )
        environment_fingerprint = _digest(
            environ.get(
                "TIANSHU_LEAN_ENVIRONMENT_FINGERPRINT",
                provenance.get("environment_fingerprint"),
            ),
            "environment_fingerprint",
        )
        fixture = provenance.get("fixture")
        if not isinstance(fixture, bool):
            raise ValueError("fixture must be boolean")
        fixture_override = environ.get("TIANSHU_LEAN_FIXTURE")
        if fixture_override is not None:
            if fixture_override not in {"true", "false"}:
                raise ValueError("TIANSHU_LEAN_FIXTURE must be exactly true or false")
            fixture = fixture_override == "true"
        max_attempts = _integer(polling.get("max_attempts"), "max_attempts", minimum=1, maximum=60)
        interval = _number(
            polling.get("interval_seconds"),
            "interval_seconds",
            minimum=0.0,
            maximum=30.0,
        )
        timeout = _number(
            polling.get("request_timeout_seconds"),
            "request_timeout_seconds",
            minimum=0.1,
            maximum=60.0,
        )
        pending = scenario.get("external_pending")
        if not isinstance(pending, list) or any(
            not isinstance(item, str) or not item.strip() for item in pending
        ):
            raise ValueError("external_pending must contain non-blank strings")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DemoRunError(f"invalid Lean Preview scenario: {exc}") from exc

    artifact_root = batch_root / "artifacts"
    artifact_root.mkdir(parents=True)
    report_path = batch_root / "demo-report.json"
    state: dict[str, object] = {
        "batch_id": batch_id,
        "source_commit": source_commit,
        "wheel_sha256": wheel_sha256,
        "environment_fingerprint": environment_fingerprint,
        "fixture": fixture,
        "external_pending": pending,
    }
    steps: list[dict[str, object]] = []

    def trace(step_id: str) -> _StepTrace:
        return _StepTrace(
            step_id=step_id,
            base_url=clean_base_url,
            token=token,
            timeout=timeout,
            transport=transport,
        )

    def submit(step_trace: _StepTrace, suffix: str, *, planned: bool) -> dict[str, str]:
        payload = dict(edict)
        payload["goal"] = f"{_text(edict.get('goal'), 'edict.goal')} [{suffix}]"
        payload["plan_review"] = planned
        key = f"lean-preview:{batch_id}:{suffix}"
        response = step_trace.request(
            "POST",
            "/api/edicts",
            body=payload,
            headers={"Idempotency-Key": key},
            accepted_statuses=(200, 202),
        )
        data = _data(response)
        metadata = _mapping(response.get("metadata"), "edict metadata")
        return {
            "edict_id": _text(data.get("id"), "edict id"),
            "memorial_id": _text(metadata.get("memorial_id"), "memorial id"),
        }

    def completed(step_trace: _StepTrace, edict_id: str) -> dict[str, object]:
        def ready(payload: dict[str, object]) -> object | None:
            value = payload.get("data")
            if value is None:
                return None
            memorial = _mapping(value, "memorial")
            status = memorial.get("status")
            if status in _TERMINAL_FAILURES:
                raise ValueError("run reached a failed terminal state")
            return memorial if status == "completed" else None

        return _mapping(
            _poll(
                step_trace,
                path=f"/api/edicts/{urllib.parse.quote(edict_id, safe='')}/memorial",
                ready=ready,
                max_attempts=max_attempts,
                interval=interval,
                sleeper=sleeper,
            ),
            "completed memorial",
        )

    def closed_bundle_for_run(
        step_trace: _StepTrace,
        *,
        edict_id: str,
        memorial_id: str,
    ) -> dict[str, object]:
        listed = step_trace.request(
            "GET", f"/api/edicts/{urllib.parse.quote(edict_id, safe='')}/evidence"
        )
        items = listed.get("items")
        if not isinstance(items, list):
            raise ValueError("evidence list items must be an array")
        closed = next(
            (
                _mapping(item, "evidence item")
                for item in items
                if isinstance(item, dict) and item.get("status") == "closed"
            ),
            None,
        )
        if closed is None:
            raise ValueError("closed Evidence Bundle was not found")
        bundle_id = _text(closed.get("bundle_id"), "Evidence Bundle id")
        downloaded = step_trace.request(
            "GET", f"/api/evidence/{urllib.parse.quote(bundle_id, safe='')}/download"
        )
        content_hash = _digest(downloaded.get("content_hash"), "Evidence Bundle hash")
        if _closed_content_hash(downloaded) != content_hash:
            raise ValueError("Evidence Bundle content hash mismatch")
        if closed.get("content_hash") != content_hash:
            raise ValueError("Evidence Bundle list/download hash mismatch")
        if (
            downloaded.get("bundle_id") != bundle_id
            or downloaded.get("edict_id") != edict_id
            or downloaded.get("memorial_id") != memorial_id
        ):
            raise ValueError("Evidence Bundle is not bound to the submitted completed run")
        return downloaded

    operations: list[Callable[[_StepTrace], object]] = []

    def doctor(step_trace: _StepTrace) -> object:
        payload = step_trace.request("GET", "/health/ready")
        if payload.get("status") not in {"ready", "degraded"}:
            raise ValueError("server is not ready")
        identity = step_trace.request("GET", "/api/auth/me")
        principal = _mapping(identity.get("principal"), "authenticated principal")
        state["principal_id"] = _text(principal.get("id"), "authenticated principal id")
        return {"status": payload["status"], "principal_id": state["principal_id"]}

    operations.append(doctor)

    def submit_initial(step_trace: _StepTrace) -> object:
        submitted = submit(step_trace, "governed", planned=True)
        state.update(submitted)
        state["submitted_memorial_id"] = submitted["memorial_id"]
        return submitted

    operations.append(submit_initial)

    def observe_decision(step_trace: _StepTrace) -> object:
        edict_id = _text(state.get("edict_id"), "edict id")

        def ready(payload: dict[str, object]) -> object | None:
            items = payload.get("items")
            if not isinstance(items, list):
                raise ValueError("decision list items must be an array")
            for item in items:
                candidate_item = _mapping(item, "decision item")
                if (
                    candidate_item.get("edict_id") == edict_id
                    and candidate_item.get("status") == "pending"
                ):
                    return candidate_item
            return None

        decision_record = _mapping(
            _poll(
                step_trace,
                path="/api/decisions?kind=plan_review&limit=100",
                ready=ready,
                max_attempts=max_attempts,
                interval=interval,
                sleeper=sleeper,
            ),
            "decision request",
        )
        state["decision_request_id"] = _text(
            decision_record.get("decision_request_id"), "decision request id"
        )
        state["decision_version"] = _integer(
            decision_record.get("version"), "decision version", minimum=1, maximum=2**31 - 1
        )
        return decision_record

    operations.append(observe_decision)

    def resolve_decision(step_trace: _StepTrace) -> object:
        request_id = _text(state.get("decision_request_id"), "decision request id")
        body = {
            "action": _text(decision.get("action"), "decision.action"),
            "reason": _text(decision.get("reason"), "decision.reason"),
            "payload": _mapping(decision.get("payload"), "decision.payload"),
            "expected_version": state["decision_version"],
        }
        payload = step_trace.request(
            "POST",
            f"/api/decisions/{urllib.parse.quote(request_id, safe='')}/resolve",
            body=body,
        )
        if payload.get("status") != "resolved":
            raise ValueError("decision was not resolved")
        return {"status": "resolved", "decision_request_id": request_id}

    operations.append(resolve_decision)

    def observe_initial_completed(step_trace: _StepTrace) -> object:
        memorial = completed(step_trace, _text(state.get("edict_id"), "edict id"))
        memorial_id = _text(memorial.get("id"), "memorial id")
        if (
            memorial_id != state["submitted_memorial_id"]
            or memorial.get("edict_id") != state["edict_id"]
        ):
            raise ValueError("completed memorial is not bound to the submitted run")
        state["memorial_id"] = memorial_id
        return {"memorial": memorial}

    operations.append(observe_initial_completed)

    def verify_bundle(step_trace: _StepTrace) -> object:
        edict_id = _text(state.get("edict_id"), "edict id")
        downloaded = closed_bundle_for_run(
            step_trace,
            edict_id=edict_id,
            memorial_id=_text(state.get("memorial_id"), "memorial id"),
        )
        bundle_id = _text(downloaded.get("bundle_id"), "Evidence Bundle id")
        content_hash = _digest(downloaded.get("content_hash"), "Evidence Bundle hash")
        state["evidence_bundle_id"] = bundle_id
        state["evidence_bundle_hash"] = content_hash
        return {"bundle": downloaded}

    operations.append(verify_bundle)

    def propose_candidate(step_trace: _StepTrace) -> object:
        body = {
            "name": _text(skill.get("name"), "skill.name"),
            "content": _text(skill.get("content"), "skill.content"),
            "evidence_bundle_ids": [],
        }
        payload = step_trace.request("POST", "/api/skills", body=body, accepted_statuses=(200, 201))
        data = _data(payload)
        if data.get("lifecycle") != "proposed":
            raise ValueError("skill candidate was not proposed")
        state["candidate_id"] = _text(data.get("candidate_id"), "candidate id")
        return {"candidate_id": state["candidate_id"], "lifecycle": "proposed"}

    operations.append(propose_candidate)

    def evaluate_gate(step_trace: _StepTrace) -> object:
        candidate_id = _text(state.get("candidate_id"), "candidate id")
        quoted = urllib.parse.quote(candidate_id, safe="")
        staged = _data(
            step_trace.request("POST", f"/api/skills/candidates/{quoted}/stage", body={})
        )
        if staged.get("lifecycle") != "staged":
            raise ValueError("skill candidate was not staged")
        candidate_before_gate = _data(
            step_trace.request("GET", f"/api/evolution/candidates/{quoted}")
        )
        evidence_bundle_ids = candidate_before_gate.get("evidence_bundle_ids")
        if (
            candidate_before_gate.get("candidate_id") != candidate_id
            or candidate_before_gate.get("kind") != "skill"
            or candidate_before_gate.get("lifecycle") != "staged"
            or not isinstance(evidence_bundle_ids, list)
            or evidence_bundle_ids
        ):
            raise ValueError("golden demo candidate must be an unbound staged skill")
        state["candidate_subject_key"] = _text(
            candidate_before_gate.get("subject_key"), "candidate subject key"
        )
        candidate_ref = _mapping(candidate_before_gate.get("candidate"), "candidate package ref")
        base_ref = _mapping(candidate_before_gate.get("base"), "champion package ref")
        for label, reference in (("candidate", candidate_ref), ("champion", base_ref)):
            _digest(reference.get("artifact_digest"), f"{label} artifact digest")
            _digest(reference.get("canonical_digest"), f"{label} canonical digest")
        state["candidate_ref"] = candidate_ref
        state["base_ref"] = base_ref
        version = _integer(
            candidate_before_gate.get("version"),
            "candidate version",
            minimum=1,
            maximum=2**31 - 1,
        )
        snapshot_version = _integer(
            candidate_before_gate.get("gate_snapshot_version"),
            "candidate gate snapshot version",
            minimum=0,
            maximum=2**31 - 1,
        )
        candidate_checks = [
            {"kind": "bash", "name": f"evolution.gate.{name}", "command": "true"}
            for name in _REQUIRED_GATE_NAMES
            if name != "evidence"
        ]
        candidate_checks.append(
            {
                "kind": "bash",
                "name": (
                    f"evolution.candidate.{candidate_id}.{version}."
                    f"{candidate_ref['artifact_digest']}"
                ),
                "command": "true",
            }
        )
        candidate_evidence_payload = dict(edict)
        candidate_evidence_payload["goal"] = (
            f"{_text(edict.get('goal'), 'edict.goal')} [candidate-evidence]"
        )
        candidate_evidence_payload["plan_review"] = False
        candidate_evidence_payload["acceptance"] = {"checks": candidate_checks}
        candidate_evidence_response = step_trace.request(
            "POST",
            "/api/edicts",
            body=candidate_evidence_payload,
            headers={"Idempotency-Key": f"lean-preview:{batch_id}:candidate-evidence"},
            accepted_statuses=(200, 202),
        )
        candidate_evidence_submitted = {
            "edict_id": _text(
                _data(candidate_evidence_response).get("id"), "candidate evidence edict id"
            ),
            "memorial_id": _text(
                _mapping(
                    candidate_evidence_response.get("metadata"),
                    "candidate evidence metadata",
                ).get("memorial_id"),
                "candidate evidence memorial id",
            ),
        }
        candidate_evidence_memorial = completed(
            step_trace, candidate_evidence_submitted["edict_id"]
        )
        if (
            candidate_evidence_memorial.get("id") != candidate_evidence_submitted["memorial_id"]
            or candidate_evidence_memorial.get("edict_id")
            != candidate_evidence_submitted["edict_id"]
        ):
            raise ValueError("candidate evidence memorial is not bound to the submitted run")
        candidate_evidence_bundle = closed_bundle_for_run(
            step_trace,
            edict_id=candidate_evidence_submitted["edict_id"],
            memorial_id=candidate_evidence_submitted["memorial_id"],
        )
        candidate_evidence_bundle_id = _text(
            candidate_evidence_bundle.get("bundle_id"), "candidate Evidence Bundle id"
        )
        gate = _data(
            step_trace.request(
                "POST",
                f"/api/skills/candidates/{quoted}/gate/evaluate",
                body={
                    "expected_version": version,
                    "evidence_bundle_ids": [candidate_evidence_bundle_id],
                },
            ),
            "gate report",
        )
        candidate = _data(step_trace.request("GET", f"/api/evolution/candidates/{quoted}"))
        candidate_evidence_bundle_ids = candidate.get("evidence_bundle_ids")
        if (
            candidate.get("candidate_id") != candidate_id
            or candidate.get("kind") != "skill"
            or candidate.get("lifecycle") != "ready"
            or candidate.get("subject_key") != state["candidate_subject_key"]
            or candidate.get("candidate") != candidate_ref
            or candidate.get("base") != base_ref
            or candidate.get("version") != version + 2
            or candidate.get("gate_snapshot_version") != snapshot_version + 1
            or candidate_evidence_bundle_ids != [*evidence_bundle_ids, candidate_evidence_bundle_id]
            or gate.get("promotion_allowed") is not True
            or gate.get("blocking_gates") != []
            or gate.get("candidate_id") != candidate_id
            or gate.get("candidate_digest") != candidate_ref.get("artifact_digest")
            or gate.get("candidate_version") != candidate.get("version")
            or gate.get("gate_snapshot_version") != candidate.get("gate_snapshot_version")
            or gate.get("evidence_bundle_ids") != candidate_evidence_bundle_ids
        ):
            raise ValueError("candidate gate did not pass")
        state["candidate_version"] = _integer(
            gate.get("candidate_version"), "gate candidate version", minimum=1, maximum=2**31 - 1
        )
        state["gate_hash"] = _canonical_hash(gate)
        state["gate_snapshot_version"] = _integer(
            gate.get("gate_snapshot_version"),
            "gate snapshot version",
            minimum=1,
            maximum=2**31 - 1,
        )
        return {
            "candidate_before_gate": candidate_before_gate,
            "candidate_evidence": {
                "submitted": candidate_evidence_submitted,
                "memorial": candidate_evidence_memorial,
                "bundle": candidate_evidence_bundle,
            },
            "candidate": candidate,
            "gate_report": gate,
        }

    operations.append(evaluate_gate)

    def start_canary(step_trace: _StepTrace) -> object:
        candidate_id = _text(state.get("candidate_id"), "candidate id")
        expected_version = _integer(
            state.get("candidate_version"),
            "candidate version",
            minimum=1,
            maximum=2**31 - 1,
        )
        body = {
            "schema_version": 1,
            "expected_version": expected_version,
            "idempotency_key": f"lean-preview:{batch_id}:canary",
            "reason": _text(canary.get("reason"), "canary.reason"),
            "allocation_basis_points": _integer(
                canary.get("allocation_basis_points"),
                "canary.allocation_basis_points",
                minimum=1,
                maximum=1000,
            ),
            "allocation_seed_id": _text(
                canary.get("allocation_seed_id"), "canary.allocation_seed_id"
            ),
            "decision_request_id": None,
        }
        receipt = _data(
            step_trace.request(
                "POST",
                f"/api/evolution/candidates/{urllib.parse.quote(candidate_id, safe='')}/canary",
                body=body,
            ),
            "canary receipt",
        )
        if (
            receipt.get("status") != "completed"
            or receipt.get("action") != "start_canary"
            or receipt.get("idempotency_key") != body["idempotency_key"]
            or receipt.get("journal_id")
            != _promotion_journal_id(
                _text(state.get("principal_id"), "authenticated principal id"),
                str(body["idempotency_key"]),
            )
            or receipt.get("candidate_id") != candidate_id
            or receipt.get("candidate_version") != expected_version + 1
            or receipt.get("gate_snapshot_version") != state["gate_snapshot_version"]
            or receipt.get("gate_report_hash") != state["gate_hash"]
            or receipt.get("lifecycle") != "canary"
            or receipt.get("allocation_basis_points") != body["allocation_basis_points"]
            or receipt.get("effect_artifact_digest") is not None
        ):
            raise ValueError("canary receipt is not bound to the request")
        state["candidate_version"] = _integer(
            receipt.get("candidate_version"),
            "canary candidate version",
            minimum=1,
            maximum=2**31 - 1,
        )
        state["canary_routing_version"] = _integer(
            receipt.get("routing_version"),
            "canary routing version",
            minimum=1,
            maximum=2**31 - 1,
        )
        return {
            "request_binding": _request_binding("start_canary", body),
            "promotion_receipt": receipt,
        }

    operations.append(start_canary)

    def submit_canary_run(step_trace: _StepTrace) -> object:
        submitted = submit(step_trace, "canary", planned=False)
        state["canary_edict_id"] = submitted["edict_id"]
        state["canary_memorial_id"] = submitted["memorial_id"]
        return submitted

    operations.append(submit_canary_run)

    def verify_candidate_overlay(step_trace: _StepTrace) -> object:
        edict_id = _text(state.get("canary_edict_id"), "canary edict id")
        memorial = completed(step_trace, edict_id)
        memorial_id = _text(memorial.get("id"), "canary memorial id")
        if (
            memorial_id != state["canary_memorial_id"]
            or memorial.get("edict_id") != state["canary_edict_id"]
        ):
            raise ValueError("canary memorial is not bound to the submitted run")
        payload = _data(
            step_trace.request(
                "GET",
                f"/api/evolution/runs/{urllib.parse.quote(memorial_id, safe='')}/assignment",
            ),
            "run assignment",
        )
        assignment = _mapping(payload.get("assignment"), "assignment")
        overlay = _mapping(payload.get("effective_overlay"), "effective overlay")
        champion_ref = _mapping(assignment.get("champion_ref"), "champion ref")
        selected_ref = _mapping(assignment.get("selected_ref"), "selected ref")
        if selected_ref == champion_ref:
            raise ValueError("run used champion instead of the real candidate overlay")
        if (
            assignment.get("memorial_id") != memorial_id
            or assignment.get("candidate_id") != state["candidate_id"]
            or assignment.get("routing_version") != state["canary_routing_version"]
            or overlay.get("assignment_id") != assignment.get("assignment_id")
            or selected_ref != state["candidate_ref"]
            or champion_ref != state["base_ref"]
            or overlay.get("kind") != "skill"
            or overlay.get("subject_key") != state["candidate_subject_key"]
            or overlay.get("artifact_digest") != selected_ref.get("artifact_digest")
            or overlay.get("canonical_digest") != selected_ref.get("canonical_digest")
        ):
            raise ValueError("effective candidate overlay is not assignment-bound")
        state["assignment_id"] = _text(assignment.get("assignment_id"), "assignment id")
        return {
            "memorial": memorial,
            "assignment": assignment,
            "effective_overlay": overlay,
        }

    operations.append(verify_candidate_overlay)

    def rollback_candidate(step_trace: _StepTrace) -> object:
        candidate_id = _text(state.get("candidate_id"), "candidate id")
        quoted = urllib.parse.quote(candidate_id, safe="")
        current = _data(step_trace.request("GET", f"/api/evolution/candidates/{quoted}"))
        if (
            current.get("candidate_id") != candidate_id
            or current.get("kind") != "skill"
            or current.get("subject_key") != state["candidate_subject_key"]
            or current.get("candidate") != state["candidate_ref"]
            or current.get("base") != state["base_ref"]
            or current.get("lifecycle") != "canary"
        ):
            raise ValueError("pre-rollback candidate is not canary-bound")
        expected_version = _integer(
            current.get("version"),
            "pre-rollback candidate version",
            minimum=1,
            maximum=2**31 - 1,
        )
        body = {
            "schema_version": 1,
            "expected_version": expected_version,
            "idempotency_key": f"lean-preview:{batch_id}:rollback",
            "reason": _text(rollback.get("reason"), "rollback.reason"),
            "decision_request_id": None,
        }
        receipt = _data(
            step_trace.request(
                "POST",
                f"/api/evolution/candidates/{quoted}/rollback",
                body=body,
            ),
            "rollback receipt",
        )
        if (
            receipt.get("status") != "completed"
            or receipt.get("action") != "rollback"
            or receipt.get("idempotency_key") != body["idempotency_key"]
            or receipt.get("journal_id")
            != _promotion_journal_id(
                _text(state.get("principal_id"), "authenticated principal id"),
                str(body["idempotency_key"]),
            )
            or receipt.get("candidate_id") != candidate_id
            or receipt.get("candidate_version") != expected_version + 2
            or receipt.get("lifecycle") != "rolled_back"
            or receipt.get("routing_version") <= state["canary_routing_version"]
            or receipt.get("allocation_basis_points") != 0
            or receipt.get("effect_artifact_digest")
            != _mapping(state.get("base_ref"), "champion package ref").get("artifact_digest")
        ):
            raise ValueError("rollback receipt does not prove zero allocation")
        state["candidate_version"] = _integer(
            receipt.get("candidate_version"),
            "rollback candidate version",
            minimum=1,
            maximum=2**31 - 1,
        )
        state["rollback_receipt_hash"] = _canonical_hash(receipt)
        state["rollback_routing_version"] = _integer(
            receipt.get("routing_version"),
            "rollback routing version",
            minimum=1,
            maximum=2**31 - 1,
        )
        return {
            "candidate_before_rollback": current,
            "request_binding": _request_binding("rollback", body),
            "rollback_receipt": receipt,
        }

    operations.append(rollback_candidate)

    def verify_post_rollback(step_trace: _StepTrace) -> object:
        submitted = submit(step_trace, "post-rollback", planned=False)
        memorial = completed(step_trace, submitted["edict_id"])
        memorial_id = _text(memorial.get("id"), "post-rollback memorial id")
        if (
            memorial_id != submitted["memorial_id"]
            or memorial.get("edict_id") != submitted["edict_id"]
        ):
            raise ValueError("post-rollback memorial is not bound to the submitted run")
        assignment_payload = _data(
            step_trace.request(
                "GET",
                f"/api/evolution/runs/{urllib.parse.quote(memorial_id, safe='')}/assignment",
            ),
            "post-rollback assignment",
        )
        assignment = _mapping(assignment_payload.get("assignment"), "post-rollback assignment")
        overlay = _mapping(
            assignment_payload.get("effective_overlay"), "post-rollback effective overlay"
        )
        champion_ref = _mapping(assignment.get("champion_ref"), "champion ref")
        selected_ref = _mapping(assignment.get("selected_ref"), "selected ref")
        candidate_id = _text(state.get("candidate_id"), "candidate id")
        candidate = _data(
            step_trace.request(
                "GET",
                f"/api/evolution/candidates/{urllib.parse.quote(candidate_id, safe='')}",
            )
        )
        routing = _mapping(candidate.get("routing"), "candidate routing")
        if (
            assignment.get("memorial_id") != memorial_id
            or assignment.get("candidate_id") != candidate_id
            or assignment.get("routing_version") != state["rollback_routing_version"]
            or overlay.get("assignment_id") != assignment.get("assignment_id")
            or selected_ref != champion_ref
            or champion_ref != state["base_ref"]
            or overlay.get("kind") != "skill"
            or overlay.get("subject_key") != state["candidate_subject_key"]
            or overlay.get("artifact_digest") != champion_ref.get("artifact_digest")
            or overlay.get("canonical_digest") != champion_ref.get("canonical_digest")
        ):
            raise ValueError("post-rollback run did not use champion")
        if (
            candidate.get("candidate_id") != candidate_id
            or candidate.get("kind") != "skill"
            or candidate.get("subject_key") != state["candidate_subject_key"]
            or candidate.get("candidate") != state["candidate_ref"]
            or candidate.get("base") != state["base_ref"]
            or candidate.get("version") != state["candidate_version"]
            or routing.get("routing_version") != state["rollback_routing_version"]
            or routing.get("allocation_basis_points") != 0
            or candidate.get("lifecycle") != "rolled_back"
        ):
            raise ValueError("post-rollback candidate does not prove zero allocation")
        return {
            "submitted": submitted,
            "memorial": memorial,
            "assignment": assignment,
            "effective_overlay": overlay,
            "candidate": candidate,
        }

    operations.append(verify_post_rollback)

    for index, (step_id, operation) in enumerate(
        zip(EXPECTED_STEP_IDS, operations, strict=True), 1
    ):
        started_at = _utc_timestamp(clock)
        step_trace = trace(step_id)
        try:
            observed = operation(step_trace)
            completed_at = _utc_timestamp(clock)
            steps.append(
                _write_step(
                    artifact_root,
                    index=index,
                    step_id=step_id,
                    status="passed",
                    started_at=started_at,
                    completed_at=completed_at,
                    trace=step_trace,
                    observed=observed,
                )
            )
        except Exception as exc:
            completed_at = _utc_timestamp(clock)
            observed = {
                "error": {
                    "code": "step_failed",
                    "type": type(exc).__name__,
                    "details_hash": hashlib.sha256(str(exc).encode("utf-8")).hexdigest(),
                }
            }
            steps.append(
                _write_step(
                    artifact_root,
                    index=index,
                    step_id=step_id,
                    status="failed",
                    started_at=started_at,
                    completed_at=completed_at,
                    trace=step_trace,
                    observed=observed,
                )
            )
            blocked_at = _utc_timestamp(clock)
            for blocked_step_id in EXPECTED_STEP_IDS[index:]:
                steps.append(_blocked_step(blocked_step_id, blocked_at))
            _write_report(report_path, state, steps)
            raise DemoRunError(
                f"Lean Preview batch {batch_id} failed at {step_id}; evidence retained"
            ) from exc

    _write_report(report_path, state, steps)
    return report_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_demo(
            base_url=args.base_url,
            scenario_path=args.scenario,
            batch_id=args.batch_id,
            output_root=args.output_root,
        )
    except DemoRunError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
