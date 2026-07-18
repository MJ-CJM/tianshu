from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tianshu.models.lean_preview import LeanPreviewDemoReportV1

ROOT = Path(__file__).parents[2]
RUNNER_PATH = ROOT / "scripts" / "run_lean_preview_demo.py"
VERIFIER_PATH = ROOT / "scripts" / "verify_lean_preview_evidence.py"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


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
    return {
        "schema_version": 1,
        "provenance": {
            "source_commit": "1" * 40,
            "wheel_sha256": DIGEST_A,
            "environment_fingerprint": DIGEST_B,
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


def _bundle() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "bundle_id": "bundle-1",
        "edict_id": "edict-1",
        "memorial_id": "memorial-1",
        "status": "closed",
        "snapshot": {"checks": [], "artifacts": []},
        "version": 2,
        "created_at": "2026-07-18T00:00:00Z",
        "closed_at": "2026-07-18T00:00:01Z",
    }
    payload["content_hash"] = _canonical_hash(payload)
    return payload


def _ref(version: str, digest: str) -> dict[str, str]:
    return {"version": version, "artifact_digest": digest, "canonical_digest": digest}


class _FakeTransport:
    def __init__(self, module, *, decision_never_ready: bool = False) -> None:
        self._module = module
        self.decision_never_ready = decision_never_ready
        self.calls: list[tuple[str, str, dict[str, str], object | None]] = []
        self.bundle = _bundle()
        self.champion = _ref("champion-v1", DIGEST_A)
        self.candidate = _ref("candidate-v1", DIGEST_B)
        self.candidate_version = 2

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: object | None,
        timeout: float,
    ):
        del timeout
        path = url.removeprefix("http://127.0.0.1:7998")
        self.calls.append((method, path, headers, copy.deepcopy(body)))
        correlation = f"corr-{len(self.calls)}"
        response_headers = {"x-correlation-id": correlation}

        if path == "/health/ready":
            payload = {"schema_version": "1", "status": "ready"}
        elif path == "/api/edicts" and method == "POST":
            number = sum(call[1] == "/api/edicts" for call in self.calls)
            payload = {
                "success": True,
                "data": {"id": f"edict-{number}"},
                "metadata": {"memorial_id": f"memorial-{number}"},
            }
        elif path.startswith("/api/decisions?"):
            payload = (
                {"items": [], "correlation_id": correlation}
                if self.decision_never_ready
                else {
                    "items": [
                        {
                            "decision_request_id": "decision-1",
                            "edict_id": "edict-1",
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
            number = path.split("/")[3].split("-")[-1]
            payload = {
                "success": True,
                "data": {"id": f"memorial-{number}", "status": "completed"},
            }
        elif path == "/api/edicts/edict-1/evidence":
            payload = {
                "items": [
                    {
                        "bundle_id": "bundle-1",
                        "status": "closed",
                        "content_hash": self.bundle["content_hash"],
                    }
                ],
                "correlation_id": correlation,
            }
        elif path == "/api/evidence/bundle-1/download":
            payload = self.bundle
            response_headers["etag"] = f'"{self.bundle["content_hash"]}"'
        elif path == "/api/skills" and method == "POST":
            payload = {
                "success": True,
                "data": {"candidate_id": "candidate-1", "lifecycle": "proposed"},
            }
        elif path == "/api/skills/candidates/candidate-1/stage":
            payload = {
                "success": True,
                "data": {"candidate_id": "candidate-1", "lifecycle": "staged"},
            }
        elif path == "/api/evolution/candidates/candidate-1" and method == "GET":
            payload = {
                "data": {
                    "candidate_id": "candidate-1",
                    "kind": "skill",
                    "version": self.candidate_version,
                    "lifecycle": "rolled_back" if self.candidate_version > 3 else "staged",
                    "base": self.champion,
                    "candidate": self.candidate,
                    "routing": {
                        "allocation_basis_points": 0 if self.candidate_version > 3 else 1000,
                        "allocation_seed_id": "lean-preview-v1",
                        "routing_version": 2,
                    },
                },
                "correlation_id": correlation,
            }
        elif path == "/api/evolution/candidates/candidate-1/gate/evaluate":
            payload = {
                "data": {
                    "candidate_id": "candidate-1",
                    "candidate_version": 3,
                    "candidate_digest": DIGEST_B,
                    "gate_snapshot_version": 1,
                    "promotion_allowed": True,
                    "blocking_gates": [],
                },
                "correlation_id": correlation,
            }
        elif path == "/api/evolution/candidates/candidate-1/canary":
            self.candidate_version = 3
            payload = {
                "data": {
                    "action": "start_canary",
                    "status": "completed",
                    "candidate_id": "candidate-1",
                    "candidate_version": 3,
                    "allocation_basis_points": 1000,
                },
                "correlation_id": correlation,
            }
        elif path == "/api/evolution/runs/memorial-2/assignment":
            payload = {
                "data": {
                    "assignment": {
                        "assignment_id": "assignment-canary",
                        "memorial_id": "memorial-2",
                        "candidate_id": "candidate-1",
                        "champion_ref": self.champion,
                        "selected_ref": self.candidate,
                    },
                    "effective_overlay": {
                        "assignment_id": "assignment-canary",
                        "artifact_digest": DIGEST_B,
                        "canonical_digest": DIGEST_B,
                    },
                },
                "correlation_id": correlation,
            }
        elif path == "/api/evolution/candidates/candidate-1/rollback":
            self.candidate_version = 4
            payload = {
                "data": {
                    "action": "rollback",
                    "status": "completed",
                    "candidate_id": "candidate-1",
                    "candidate_version": 4,
                    "allocation_basis_points": 0,
                    "effect_artifact_digest": DIGEST_A,
                },
                "correlation_id": correlation,
            }
        elif path == "/api/evolution/runs/memorial-3/assignment":
            payload = {
                "data": {
                    "assignment": {
                        "assignment_id": "assignment-post-rollback",
                        "memorial_id": "memorial-3",
                        "candidate_id": "candidate-1",
                        "champion_ref": self.champion,
                        "selected_ref": self.champion,
                    },
                    "effective_overlay": {
                        "assignment_id": "assignment-post-rollback",
                        "artifact_digest": DIGEST_A,
                        "canonical_digest": DIGEST_A,
                    },
                },
                "correlation_id": correlation,
            }
        else:  # pragma: no cover - a runner API drift should show the unexpected call
            raise AssertionError(f"unexpected request: {method} {path}")

        if (
            isinstance(payload, dict)
            and "correlation_id" not in payload
            and path != "/api/evidence/bundle-1/download"
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
    assert report["assignment_id"] == "assignment-canary"
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
