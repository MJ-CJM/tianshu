from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tianshu.evidence.models import ClosedEvidenceBundleV1
from tianshu.evolution.gates import EvolutionGateReportV1
from tianshu.evolution.promotion import PromotionReceiptV1, RollbackReceiptV1
from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.evolution_candidate import EvolutionCandidateV1
from tianshu.models.lean_preview import LeanPreviewDemoReportV1
from tianshu.models.run_assignment import EffectiveEvolutionOverlayV1, RunAssignmentV1

ROOT = Path(__file__).parents[2]
RUNNER_PATH = ROOT / "src" / "tianshu" / "lean_preview_demo.py"
VERIFIER_PATH = ROOT / "scripts" / "verify_lean_preview_evidence.py"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
S5_EVIDENCE_PATH = ROOT / "docs" / "cc-fable-v1" / "evidence" / "s5-lean-evolution.json"


def _s5_evidence() -> dict[str, object]:
    return json.loads(S5_EVIDENCE_PATH.read_text(encoding="utf-8"))


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _promotion_journal_id(principal_id: str, idempotency_key: str) -> str:
    identity = _canonical_hash({"principal_id": principal_id, "idempotency_key": idempotency_key})
    command_key = f"promotion:{identity}"
    return hashlib.sha256(f"{command_key}\0completed".encode()).hexdigest()


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _strict_json(model_type, value: object):
    return model_type.model_validate_json(json.dumps(value))


def _module():
    spec = importlib.util.spec_from_file_location("lean_preview_runner", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verifier_module():
    spec = importlib.util.spec_from_file_location("lean_preview_verifier", VERIFIER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scenario() -> dict[str, object]:
    bundle = _strict_json(ClosedEvidenceBundleV1, _s5_evidence()["gate_bundle"])
    return {
        "schema_version": 1,
        "provenance": {
            "source_commit": "1" * 40,
            "wheel_sha256": DIGEST_A,
            "environment_fingerprint": bundle.snapshot.environment.environment_fingerprint,
            "fixture": True,
        },
        "polling": {
            "interval_seconds": 0.01,
            "max_attempts": 3,
            "request_timeout_seconds": 1.0,
        },
        "auth_token_env": "TIANSHU_BOOTSTRAP_TOKEN",
        "edict": {
            "goal": "Create the deterministic Lean Preview artifact",
            "plan_review": True,
        },
        "decision": {
            "action": "approve",
            "reason": "Approved for the bounded offline demo",
            "payload": {"schema_version": 1},
        },
        "skill": {
            "name": "lean-preview-helper",
            "content": "---\nname: lean-preview-helper\ndescription: Demo helper\n---\n\nCandidate.",
        },
        "canary": {
            "allocation_basis_points": 1000,
            "allocation_seed_id": "lean-preview-v1",
            "reason": "Bounded deterministic canary",
        },
        "rollback": {"reason": "Golden demo rollback proof"},
        "external_pending": ["voiceover", "external_executor"],
    }


class _FakeTransport:
    def __init__(
        self,
        module,
        *,
        decision_never_ready: bool = False,
        candidate_is_code: bool = False,
        receipt_key_mismatch: str | None = None,
        canary_overlay_subject_mismatch: bool = False,
        adversary: str | None = None,
    ) -> None:
        self._module = module
        self.decision_never_ready = decision_never_ready
        self.receipt_key_mismatch = receipt_key_mismatch
        self.principal_id = "user:owner"
        self.calls: list[tuple[str, str, dict[str, str], object | None]] = []
        evidence = _s5_evidence()
        self.candidate_bundle = _strict_json(
            ClosedEvidenceBundleV1, evidence["gate_bundle"]
        ).model_dump(mode="json")
        self.bundle = copy.deepcopy(self.candidate_bundle)
        self.bundle["bundle_id"] = "evidence:initial-governed-run"
        self.bundle["edict_id"] = "edict:initial-governed-run"
        self.bundle["memorial_id"] = "memorial:initial-governed-run"
        self.bundle.pop("content_hash")
        self.bundle["content_hash"] = _canonical_hash(self.bundle)
        self.candidate_staged = _strict_json(
            EvolutionCandidateV1, evidence["ready_candidate"]
        ).model_dump(mode="json")
        self.candidate_staged.update(
            {
                "lifecycle": "staged",
                "version": 2,
                "gate_snapshot_version": 0,
                "evidence_bundle_ids": [],
                "updated_at": "2026-07-18T09:00:01Z",
            }
        )
        self.candidate_ready = _strict_json(
            EvolutionCandidateV1, evidence["ready_candidate"]
        ).model_dump(mode="json")
        self.candidate_final = _strict_json(
            EvolutionCandidateV1, evidence["final_candidate"]
        ).model_dump(mode="json")
        self.gate = _strict_json(EvolutionGateReportV1, evidence["gate_report"]).model_dump(
            mode="json"
        )
        promotion = _mapping(evidence["promotion_actions"])
        self.canary_receipt = _strict_json(
            PromotionReceiptV1, promotion["start_receipt"]
        ).model_dump(mode="json")
        self.rollback_receipt = _strict_json(
            RollbackReceiptV1, promotion["rollback_receipt"]
        ).model_dump(mode="json")
        self.candidate_canary = copy.deepcopy(self.candidate_ready)
        self.candidate_canary["lifecycle"] = "canary"
        self.candidate_canary["version"] = self.rollback_receipt["candidate_version"] - 2
        self.candidate_canary["routing"] = {
            "allocation_basis_points": self.canary_receipt["allocation_basis_points"],
            "allocation_seed_id": "lean-preview-v1",
            "routing_version": self.canary_receipt["routing_version"],
        }
        self.candidate_canary["updated_at"] = self.canary_receipt["completed_at"]
        assignment_evidence = _mapping(evidence["assignment_evidence"])
        self.canary_assignment = _strict_json(
            RunAssignmentV1, assignment_evidence["assignment"]
        ).model_dump(mode="json")
        self.canary_overlay = _strict_json(
            EffectiveEvolutionOverlayV1, assignment_evidence["overlay"]
        ).model_dump(mode="json")
        final_candidate = _strict_json(EvolutionCandidateV1, evidence["final_candidate"])
        self.post_assignment = RunAssignmentV1(
            assignment_id="assignment:post-rollback",
            memorial_id="memorial:post-rollback",
            candidate_id=final_candidate.candidate_id,
            champion_ref=final_candidate.base,
            selected_ref=final_candidate.base,
            routing_version=final_candidate.routing.routing_version,
            bucket=9999,
            created_at=final_candidate.updated_at,
        ).model_dump(mode="json")
        self.post_overlay = EffectiveEvolutionOverlayV1(
            assignment_id=self.post_assignment["assignment_id"],
            kind=final_candidate.kind,
            subject_key=final_candidate.subject_key,
            artifact_digest=final_candidate.base.artifact_digest,
            canonical_digest=final_candidate.base.canonical_digest,
        ).model_dump(mode="json")
        if candidate_is_code:
            for candidate in (
                self.candidate_staged,
                self.candidate_ready,
                self.candidate_canary,
                self.candidate_final,
            ):
                candidate["kind"] = "code"
                candidate["evolution_contract"]["kind"] = "code"
                candidate["evolution_contract_hash"] = _canonical_hash(
                    candidate["evolution_contract"]
                )
            self.canary_overlay["kind"] = "code"
            self.post_overlay["kind"] = "code"
        if canary_overlay_subject_mismatch:
            self.canary_overlay["subject_key"] = "skill:other"
        self.initial_edict = Edict(
            id=self.bundle["edict_id"], goal="Lean Preview governed run"
        ).model_dump(mode="json")
        self.initial_memorial = Memorial(
            id=self.bundle["memorial_id"],
            edict_id=self.bundle["edict_id"],
            status=TaskStatus.COMPLETED,
        ).model_dump(mode="json")
        self.candidate_evidence_edict = Edict(
            id=self.candidate_bundle["edict_id"],
            goal="Lean Preview candidate evidence run",
        ).model_dump(mode="json")
        self.candidate_evidence_memorial = Memorial(
            id=self.candidate_bundle["memorial_id"],
            edict_id=self.candidate_bundle["edict_id"],
            status=TaskStatus.COMPLETED,
        ).model_dump(mode="json")
        self.canary_edict = Edict(id="edict:canary", goal="Lean Preview canary run").model_dump(
            mode="json"
        )
        self.canary_memorial = Memorial(
            id=self.canary_assignment["memorial_id"],
            edict_id=self.canary_edict["id"],
            status=TaskStatus.COMPLETED,
        ).model_dump(mode="json")
        self.post_edict = Edict(
            id="edict:post-rollback", goal="Lean Preview post-rollback run"
        ).model_dump(mode="json")
        self.post_memorial = Memorial(
            id=self.post_assignment["memorial_id"],
            edict_id=self.post_edict["id"],
            status=TaskStatus.COMPLETED,
        ).model_dump(mode="json")
        self.served_bundle = copy.deepcopy(self.bundle)
        self.served_gate = copy.deepcopy(self.gate)
        self.served_canary_assignment = copy.deepcopy(self.canary_assignment)
        self.served_canary_overlay = copy.deepcopy(self.canary_overlay)
        self.served_post_assignment = copy.deepcopy(self.post_assignment)
        self.served_post_overlay = copy.deepcopy(self.post_overlay)
        if adversary == "bundle_other_run":
            self.served_bundle["edict_id"] = "edict:other-run"
            self.served_bundle["memorial_id"] = "memorial:other-run"
            self.served_bundle.pop("content_hash")
            self.served_bundle["content_hash"] = _canonical_hash(self.served_bundle)
        elif adversary == "gate_candidate_id":
            self.served_gate["candidate_id"] = "candidate:other"
        elif adversary == "gate_digest":
            self.served_gate["candidate_digest"] = DIGEST_A
        elif adversary == "gate_version":
            self.served_gate["candidate_version"] += 1
        elif adversary == "gate_evidence_ids":
            self.served_gate["evidence_bundle_ids"] = ["evidence:other"]
        elif adversary == "canary_memorial":
            self.served_canary_assignment["memorial_id"] = "memorial:other"
        elif adversary == "canary_candidate":
            self.served_canary_assignment["candidate_id"] = "candidate:other"
        elif adversary == "canary_routing":
            self.served_canary_assignment["routing_version"] += 1
        elif adversary == "canary_overlay_assignment":
            self.served_canary_overlay["assignment_id"] = "assignment:other"
        elif adversary == "post_memorial":
            self.served_post_assignment["memorial_id"] = "memorial:other"
        elif adversary == "post_candidate":
            self.served_post_assignment["candidate_id"] = "candidate:other"
        elif adversary == "post_routing":
            self.served_post_assignment["routing_version"] += 1
        elif adversary == "post_overlay_assignment":
            self.served_post_overlay["assignment_id"] = "assignment:other"
        self.rolled_back = False
        self.canary_started = False
        self.gate_evaluated = False

    @property
    def edicts(self) -> tuple[dict[str, object], ...]:
        return (
            self.initial_edict,
            self.candidate_evidence_edict,
            self.canary_edict,
            self.post_edict,
        )

    @property
    def memorials(self) -> dict[str, dict[str, object]]:
        return {
            str(self.initial_edict["id"]): self.initial_memorial,
            str(self.candidate_evidence_edict["id"]): self.candidate_evidence_memorial,
            str(self.canary_edict["id"]): self.canary_memorial,
            str(self.post_edict["id"]): self.post_memorial,
        }

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: object | None,
        timeout: float,
    ):
        del timeout
        path = urllib.parse.unquote(url.removeprefix("http://127.0.0.1:7998"))
        self.calls.append((method, path, headers, copy.deepcopy(body)))
        correlation = f"corr-{len(self.calls)}"
        response_headers = {"x-correlation-id": correlation}
        payload: dict[str, object]

        if path == "/health/ready":
            payload = {"schema_version": "1", "status": "ready"}
        elif path == "/api/auth/me":
            payload = {
                "principal": {"id": self.principal_id},
                "source": "bearer",
                "client_kind": "api",
            }
        elif path == "/api/edicts" and method == "POST":
            number = sum(call[1] == "/api/edicts" for call in self.calls)
            edict = self.edicts[number - 1]
            memorial = self.memorials[str(edict["id"])]
            payload = {
                "success": True,
                "data": edict,
                "metadata": {"memorial_id": memorial["id"]},
            }
        elif path.startswith("/api/decisions?"):
            payload = (
                {"items": [], "correlation_id": correlation}
                if self.decision_never_ready
                else {
                    "items": [
                        {
                            "decision_request_id": "decision-1",
                            "edict_id": self.initial_edict["id"],
                            "status": "pending",
                            "version": 1,
                        }
                    ],
                    "correlation_id": correlation,
                }
            )
        elif path == "/api/decisions/decision-1/resolve":
            payload = {"status": "resolved", "correlation_id": correlation}
        elif path.startswith("/api/edicts/") and path.endswith("/memorial"):
            edict_id = path.removeprefix("/api/edicts/").removesuffix("/memorial")
            payload = {
                "success": True,
                "data": self.memorials[edict_id],
            }
        elif path == f"/api/edicts/{self.initial_edict['id']}/evidence":
            payload = {
                "items": [
                    {
                        "bundle_id": self.bundle["bundle_id"],
                        "memorial_id": self.served_bundle["memorial_id"],
                        "status": "closed",
                        "content_hash": self.served_bundle["content_hash"],
                    }
                ],
                "correlation_id": correlation,
            }
        elif path == f"/api/edicts/{self.candidate_evidence_edict['id']}/evidence":
            payload = {
                "items": [
                    {
                        "bundle_id": self.candidate_bundle["bundle_id"],
                        "memorial_id": self.candidate_bundle["memorial_id"],
                        "status": "closed",
                        "content_hash": self.candidate_bundle["content_hash"],
                    }
                ],
                "correlation_id": correlation,
            }
        elif path == f"/api/evidence/{self.bundle['bundle_id']}/download":
            payload = self.served_bundle
            response_headers["etag"] = f'"{self.served_bundle["content_hash"]}"'
        elif path == f"/api/evidence/{self.candidate_bundle['bundle_id']}/download":
            payload = self.candidate_bundle
            response_headers["etag"] = f'"{self.candidate_bundle["content_hash"]}"'
        elif path == "/api/skills" and method == "POST":
            payload = {
                "success": True,
                "data": {
                    "candidate_id": self.candidate_ready["candidate_id"],
                    "lifecycle": "proposed",
                },
            }
        elif path == f"/api/skills/candidates/{self.candidate_ready['candidate_id']}/stage":
            payload = {
                "success": True,
                "data": {
                    "candidate_id": self.candidate_ready["candidate_id"],
                    "lifecycle": "staged",
                },
            }
        elif (
            path == f"/api/evolution/candidates/{self.candidate_ready['candidate_id']}"
            and method == "GET"
        ):
            payload = {
                "data": (
                    self.candidate_final
                    if self.rolled_back
                    else self.candidate_canary
                    if self.canary_started
                    else self.candidate_ready
                    if self.gate_evaluated
                    else self.candidate_staged
                ),
                "correlation_id": correlation,
            }
        elif path == f"/api/skills/candidates/{self.candidate_ready['candidate_id']}/gate/evaluate":
            assert isinstance(body, dict)
            assert body["expected_version"] == self.candidate_staged["version"]
            assert body["evidence_bundle_ids"] == [self.candidate_bundle["bundle_id"]]
            self.gate_evaluated = True
            payload = {
                "data": self.served_gate,
                "correlation_id": correlation,
            }
        elif path == f"/api/evolution/candidates/{self.candidate_ready['candidate_id']}/canary":
            assert isinstance(body, dict)
            self.canary_started = True
            receipt = copy.deepcopy(self.canary_receipt)
            key = str(body["idempotency_key"])
            if self.receipt_key_mismatch == "canary":
                key = f"{key}:spliced"
            receipt["idempotency_key"] = key
            receipt["journal_id"] = _promotion_journal_id(self.principal_id, key)
            payload = {
                "data": receipt,
                "correlation_id": correlation,
            }
        elif path == f"/api/evolution/runs/{self.canary_assignment['memorial_id']}/assignment":
            payload = {
                "data": {
                    "assignment": self.served_canary_assignment,
                    "effective_overlay": self.served_canary_overlay,
                },
                "correlation_id": correlation,
            }
        elif path == (f"/api/evolution/candidates/{self.candidate_ready['candidate_id']}/rollback"):
            self.rolled_back = True
            assert isinstance(body, dict)
            receipt = copy.deepcopy(self.rollback_receipt)
            key = str(body["idempotency_key"])
            if self.receipt_key_mismatch == "rollback":
                key = f"{key}:spliced"
            receipt["idempotency_key"] = key
            receipt["journal_id"] = _promotion_journal_id(self.principal_id, key)
            payload = {
                "data": receipt,
                "correlation_id": correlation,
            }
        elif path == f"/api/evolution/runs/{self.post_assignment['memorial_id']}/assignment":
            payload = {
                "data": {
                    "assignment": self.served_post_assignment,
                    "effective_overlay": self.served_post_overlay,
                },
                "correlation_id": correlation,
            }
        else:  # pragma: no cover - a runner API drift should show the unexpected call
            raise AssertionError(f"unexpected request: {method} {path}")

        if (
            isinstance(payload, dict)
            and "correlation_id" not in payload
            and not path.startswith("/api/evidence/")
        ):
            payload = {**payload, "correlation_id": correlation}
        return self._module.HttpResponse(200, response_headers, _canonical_bytes(payload))


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 18, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.value += timedelta(seconds=1)
        return self.value


def test_runner_uses_only_stdlib_and_public_http_surfaces(tmp_path: Path) -> None:
    tree = ast.parse(RUNNER_PATH.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "tianshu" not in imported
    assert imported <= {
        "__future__",
        "argparse",
        "collections",
        "dataclasses",
        "datetime",
        "hashlib",
        "json",
        "os",
        "pathlib",
        "sys",
        "time",
        "typing",
        "urllib",
    }

    module = _module()
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(_scenario()), encoding="utf-8")
    transport = _FakeTransport(module)

    report_path = module.run_demo(
        base_url="http://127.0.0.1:7998",
        scenario_path=scenario_path,
        batch_id="batch-public-boundary",
        output_root=tmp_path / "evidence",
        transport=transport,
        clock=_Clock(),
        sleeper=lambda _seconds: None,
        environ={
            "TIANSHU_BOOTSTRAP_TOKEN": "super-secret-token",
            "TIANSHU_LEAN_FIXTURE": "false",
        },
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert [step["step_id"] for step in report["steps"]] == list(module.EXPECTED_STEP_IDS)
    assert {step["status"] for step in report["steps"]} == {"passed"}
    assert report["assignment_id"] == transport.canary_assignment["assignment_id"]
    assert report["fixture"] is False
    assert report["evidence_bundle_hash"] == transport.bundle["content_hash"]
    assert report["content_hash"] == _canonical_hash(
        {key: value for key, value in report.items() if key != "content_hash"}
    )
    LeanPreviewDemoReportV1.model_validate_json(report_path.read_text(encoding="utf-8"))
    _verifier_module().verify_demo_evidence(
        report_path,
        report_path.parent / "artifacts",
        expected_source_commit="1" * 40,
        expected_wheel_sha256=DIGEST_A,
    )

    observed_by_step = {
        step_id: json.loads(
            (report_path.parent / "artifacts" / f"{index:02d}-{step_id}.json").read_text(
                encoding="utf-8"
            )
        )["observed"]
        for index, step_id in enumerate(module.EXPECTED_STEP_IDS, 1)
    }
    _strict_json(Memorial, observed_by_step["observe_completed_run"]["memorial"])
    _strict_json(ClosedEvidenceBundleV1, observed_by_step["verify_evidence_bundle"]["bundle"])
    _strict_json(
        EvolutionCandidateV1,
        observed_by_step["evaluate_candidate_gate"]["candidate_before_gate"],
    )
    _strict_json(
        ClosedEvidenceBundleV1,
        observed_by_step["evaluate_candidate_gate"]["candidate_evidence"]["bundle"],
    )
    _strict_json(EvolutionCandidateV1, observed_by_step["evaluate_candidate_gate"]["candidate"])
    _strict_json(EvolutionGateReportV1, observed_by_step["evaluate_candidate_gate"]["gate_report"])
    _strict_json(PromotionReceiptV1, observed_by_step["start_skill_canary"]["promotion_receipt"])
    _strict_json(Memorial, observed_by_step["verify_real_candidate_overlay"]["memorial"])
    _strict_json(RunAssignmentV1, observed_by_step["verify_real_candidate_overlay"]["assignment"])
    _strict_json(
        EffectiveEvolutionOverlayV1,
        observed_by_step["verify_real_candidate_overlay"]["effective_overlay"],
    )
    _strict_json(RollbackReceiptV1, observed_by_step["rollback_candidate"]["rollback_receipt"])
    _strict_json(Memorial, observed_by_step["verify_new_run_uses_champion"]["memorial"])
    _strict_json(RunAssignmentV1, observed_by_step["verify_new_run_uses_champion"]["assignment"])
    _strict_json(
        EffectiveEvolutionOverlayV1,
        observed_by_step["verify_new_run_uses_champion"]["effective_overlay"],
    )
    _strict_json(
        EvolutionCandidateV1, observed_by_step["verify_new_run_uses_champion"]["candidate"]
    )
    assert observed_by_step["doctor_ready"]["principal_id"] == transport.principal_id
    before_gate = observed_by_step["evaluate_candidate_gate"]["candidate_before_gate"]
    ready = observed_by_step["evaluate_candidate_gate"]["candidate"]
    assert before_gate["lifecycle"] == "staged"
    assert before_gate["version"] == 2
    assert before_gate["gate_snapshot_version"] == 0
    assert ready["lifecycle"] == "ready"
    assert ready["version"] == 4
    assert ready["gate_snapshot_version"] == 1
    assert ready["evidence_bundle_ids"] == [transport.candidate_bundle["bundle_id"]]
    gate_calls = [call for call in transport.calls if call[1].endswith("/gate/evaluate")]
    assert [call[1] for call in gate_calls] == [
        f"/api/skills/candidates/{ready['candidate_id']}/gate/evaluate"
    ]
    for step_id, action in (
        ("start_skill_canary", "start_canary"),
        ("rollback_candidate", "rollback"),
    ):
        observed = observed_by_step[step_id]
        request_binding = observed["request_binding"]
        receipt_name = "promotion_receipt" if action == "start_canary" else "rollback_receipt"
        receipt = observed[receipt_name]
        expected_key = f"lean-preview:batch-public-boundary:{'canary' if action == 'start_canary' else 'rollback'}"
        assert request_binding["action"] == action
        assert request_binding["idempotency_key"] == expected_key
        assert receipt["idempotency_key"] == expected_key
        assert receipt["journal_id"] == _promotion_journal_id(transport.principal_id, expected_key)

    artifacts = sorted((report_path.parent / "artifacts").glob("*.json"))
    assert len(artifacts) == 13
    joined = "\n".join(path.read_text(encoding="utf-8") for path in artifacts)
    assert "super-secret-token" not in joined
    assert _scenario()["skill"]["content"] not in joined
    assert _scenario()["decision"]["reason"] not in joined
    for artifact in artifacts:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        assert payload["requests"]
        for request in payload["requests"]:
            assert set(request) == {"method", "path", "body_sha256"}
        assert all(payload["correlation_ids"])

    api_calls = [call for call in transport.calls if call[1].startswith("/api/")]
    assert all(call[2]["Authorization"] == "Bearer super-secret-token" for call in api_calls)


def test_runner_bounds_polling_and_retains_failed_batch(tmp_path: Path) -> None:
    module = _module()
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(_scenario()), encoding="utf-8")
    transport = _FakeTransport(module, decision_never_ready=True)

    with pytest.raises(module.DemoRunError, match="observe_decision_required"):
        module.run_demo(
            base_url="http://127.0.0.1:7998",
            scenario_path=scenario_path,
            batch_id="batch-timeout-retained",
            output_root=tmp_path / "evidence",
            transport=transport,
            clock=_Clock(),
            sleeper=lambda _seconds: None,
            environ={"TIANSHU_BOOTSTRAP_TOKEN": "secret"},
        )

    decision_polls = [call for call in transport.calls if call[1].startswith("/api/decisions?")]
    assert len(decision_polls) == 3
    report_path = tmp_path / "evidence" / "batch-timeout-retained" / "demo-report.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    statuses = [step["status"] for step in report["steps"]]
    assert statuses[:3] == ["passed", "passed", "failed"]
    assert statuses[3:] == ["blocked"] * 10
    assert (report_path.parent / "artifacts" / "03-observe_decision_required.json").is_file()


def test_runner_refuses_to_overwrite_an_existing_batch(tmp_path: Path) -> None:
    module = _module()
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(_scenario()), encoding="utf-8")
    retained = tmp_path / "evidence" / "same-batch"
    retained.mkdir(parents=True)
    marker = retained / "keep.txt"
    marker.write_text("retained", encoding="utf-8")

    with pytest.raises(module.DemoRunError, match="already exists"):
        module.run_demo(
            base_url="http://127.0.0.1:7998",
            scenario_path=scenario_path,
            batch_id="same-batch",
            output_root=tmp_path / "evidence",
            transport=_FakeTransport(module),
            environ={"TIANSHU_BOOTSTRAP_TOKEN": "secret"},
        )
    assert marker.read_text(encoding="utf-8") == "retained"


@pytest.mark.parametrize(
    ("transport_kwargs", "failed_step"),
    [
        ({"candidate_is_code": True}, "evaluate_candidate_gate"),
        ({"canary_overlay_subject_mismatch": True}, "verify_real_candidate_overlay"),
        ({"receipt_key_mismatch": "canary"}, "start_skill_canary"),
        ({"receipt_key_mismatch": "rollback"}, "rollback_candidate"),
    ],
)
def test_runner_rejects_non_skill_or_request_unbound_public_responses(
    tmp_path: Path,
    transport_kwargs: dict[str, object],
    failed_step: str,
) -> None:
    module = _module()
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(_scenario()), encoding="utf-8")

    with pytest.raises(module.DemoRunError, match=failed_step):
        module.run_demo(
            base_url="http://127.0.0.1:7998",
            scenario_path=scenario_path,
            batch_id=f"batch-{failed_step}",
            output_root=tmp_path / "evidence",
            transport=_FakeTransport(module, **transport_kwargs),
            clock=_Clock(),
            sleeper=lambda _seconds: None,
            environ={"TIANSHU_BOOTSTRAP_TOKEN": "secret"},
        )


@pytest.mark.parametrize(
    ("adversary", "failed_step"),
    [
        ("bundle_other_run", "verify_evidence_bundle"),
        ("gate_candidate_id", "evaluate_candidate_gate"),
        ("gate_digest", "evaluate_candidate_gate"),
        ("gate_version", "evaluate_candidate_gate"),
        ("gate_evidence_ids", "evaluate_candidate_gate"),
        ("canary_memorial", "verify_real_candidate_overlay"),
        ("canary_candidate", "verify_real_candidate_overlay"),
        ("canary_routing", "verify_real_candidate_overlay"),
        ("canary_overlay_assignment", "verify_real_candidate_overlay"),
        ("post_memorial", "verify_new_run_uses_champion"),
        ("post_candidate", "verify_new_run_uses_champion"),
        ("post_routing", "verify_new_run_uses_champion"),
        ("post_overlay_assignment", "verify_new_run_uses_champion"),
    ],
)
def test_runner_rejects_cross_run_gate_and_assignment_splices_at_collection_time(
    tmp_path: Path, adversary: str, failed_step: str
) -> None:
    module = _module()
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(_scenario()), encoding="utf-8")
    batch_id = f"batch-{adversary}"

    with pytest.raises(module.DemoRunError, match=failed_step):
        module.run_demo(
            base_url="http://127.0.0.1:7998",
            scenario_path=scenario_path,
            batch_id=batch_id,
            output_root=tmp_path / "evidence",
            transport=_FakeTransport(module, adversary=adversary),
            clock=_Clock(),
            sleeper=lambda _seconds: None,
            environ={"TIANSHU_BOOTSTRAP_TOKEN": "secret"},
        )

    report_path = tmp_path / "evidence" / batch_id / "demo-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    statuses = [step["status"] for step in report["steps"]]
    failed_index = list(module.EXPECTED_STEP_IDS).index(failed_step)
    assert statuses[:failed_index] == ["passed"] * failed_index
    assert statuses[failed_index] == "failed"
    assert statuses[failed_index + 1 :] == ["blocked"] * (12 - failed_index)
    assert statuses != ["passed"] * 13
