"""Governed-workspace CLI surfaces and apply-token handling."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from typer.testing import CliRunner

from tianshu.cli.main import app

runner = CliRunner()
_BASE = "https://tianshu.example.com"
_TOKEN = "A9fK7zQ4mV2pL8xR6cN3wH5jD1sB0yUe"


@pytest.fixture(autouse=True)
def _isolated_cli(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("TIANSHU_API_URL", _BASE)
    monkeypatch.setenv("TIANSHU_CREDENTIAL_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.delenv("TIANSHU_API_TOKEN", raising=False)


def _status_payload() -> dict:
    return {
        "success": True,
        "data": {
            "run_id": "run-1",
            "lease_id": "lease-1",
            "state": "active",
            "apply_mode": "governed",
        },
    }


def _preview_payload() -> dict:
    return {
        "success": True,
        "data": {
            "change_set_id": "changes-1",
            "change_set_hash": "a" * 64,
            "changes": [
                {
                    "kind": "modify",
                    "old_path": "before.txt",
                    "new_path": "after.txt",
                    "old_mode": "100644",
                    "new_mode": "100644",
                    "old_size": 5,
                    "new_size": 6,
                    "binary": False,
                }
            ],
        },
    }


def _receipt_payload() -> dict:
    return {
        "success": True,
        "data": {
            "receipt": {
                "id": "receipt-1",
                "outcome": "succeeded",
                "rollback_status": "not_required",
                "unsafe_echo": _TOKEN,
                f"reflected-{_TOKEN}": "unsafe-key",
            }
        },
    }


@pytest.mark.parametrize(
    ("command", "payload", "expected"),
    [
        (["workspace", "status", "run-1"], _status_payload(), "lease-1"),
        (["workspace", "preview", "run-1"], _preview_payload(), "after.txt"),
    ],
)
def test_workspace_read_commands_render_stable_tables(
    respx_mock,
    command: list[str],
    payload: dict,
    expected: str,
) -> None:
    suffix = "/status" if command[1] == "status" else "/changes"
    respx_mock.get(f"{_BASE}/api/workspace-runs/run-1{suffix}").respond(200, json=payload)

    result = runner.invoke(app, command)

    assert result.exit_code == 0, result.output
    assert expected in result.output


@pytest.mark.parametrize(
    ("subcommand", "payload"),
    [
        ("status", _status_payload()),
        ("preview", _preview_payload()),
    ],
)
def test_workspace_read_commands_emit_canonical_json(
    respx_mock,
    subcommand: str,
    payload: dict,
) -> None:
    suffix = "/status" if subcommand == "status" else "/changes"
    respx_mock.get(f"{_BASE}/api/workspace-runs/run-1{suffix}").respond(200, json=payload)

    result = runner.invoke(app, ["workspace", subcommand, "run-1", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == payload
    assert result.stdout == json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"


def test_apply_help_has_no_raw_token_argument_or_option() -> None:
    result = runner.invoke(app, ["workspace", "apply", "--help"])
    rejected = runner.invoke(
        app,
        [
            "workspace",
            "apply",
            "run-1",
            "--decision-id",
            "decision-1",
            "--token",
            _TOKEN,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "--token-stdin" in result.output
    assert "--token " not in result.output
    assert "TOKEN" not in result.output.replace("--token-stdin", "")
    assert rejected.exit_code == 2
    assert _TOKEN not in rejected.output


@pytest.mark.parametrize("stdin_mode", [False, True], ids=("hidden-prompt", "stdin"))
def test_apply_token_is_body_only_and_never_echoed_or_logged(
    respx_mock,
    caplog: pytest.LogCaptureFixture,
    stdin_mode: bool,
    tmp_path,
) -> None:
    route = respx_mock.post(f"{_BASE}/api/workspace-runs/run-1/apply").respond(
        200,
        json=_receipt_payload(),
    )
    command = ["workspace", "apply", "run-1", "--decision-id", "decision-1"]
    if stdin_mode:
        command.append("--token-stdin")
    caplog.set_level(logging.DEBUG)

    result = runner.invoke(app, command, input=f"{_TOKEN}\n")

    assert result.exit_code == 0, result.output
    assert _TOKEN not in result.output
    assert _TOKEN not in caplog.text
    request = route.calls[0].request
    assert str(request.url) == f"{_BASE}/api/workspace-runs/run-1/apply"
    assert _TOKEN not in str(request.url)
    assert _TOKEN not in "\n".join(f"{key}: {value}" for key, value in request.headers.items())
    assert json.loads(request.content) == {"decision_id": "decision-1", "token": _TOKEN}
    assert not (tmp_path / "credentials.json").exists()


@pytest.mark.parametrize("status_code", [401, 403, 409, 422, 500])
def test_apply_error_never_echoes_server_reflected_token(
    respx_mock,
    caplog: pytest.LogCaptureFixture,
    status_code: int,
) -> None:
    respx_mock.post(f"{_BASE}/api/workspace-runs/run-1/apply").respond(
        status_code,
        json={"detail": {"code": "apply_denied", "input": {"token": _TOKEN}}},
    )
    caplog.set_level(logging.DEBUG)

    result = runner.invoke(
        app,
        [
            "workspace",
            "apply",
            "run-1",
            "--decision-id",
            "decision-1",
            "--token-stdin",
        ],
        input=f"{_TOKEN}\n",
    )

    assert result.exit_code == 1
    assert f"({status_code})" in result.output
    assert _TOKEN not in result.output
    assert _TOKEN not in caplog.text


def test_apply_network_error_never_echoes_token(
    respx_mock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx_mock.post(f"{_BASE}/api/workspace-runs/run-1/apply").mock(
        side_effect=httpx.ConnectError(f"transport rejected {_TOKEN}")
    )
    caplog.set_level(logging.DEBUG)

    result = runner.invoke(
        app,
        [
            "workspace",
            "apply",
            "run-1",
            "--decision-id",
            "decision-1",
            "--token-stdin",
        ],
        input=f"{_TOKEN}\n",
    )

    assert result.exit_code == 1
    assert _TOKEN not in result.output
    assert _TOKEN not in caplog.text


def test_apply_token_stdin_refuses_tty_before_reading_or_requesting(
    monkeypatch,
    respx_mock,
) -> None:
    from tianshu.cli.commands import workspace

    route = respx_mock.post(f"{_BASE}/api/workspace-runs/run-1/apply").respond(
        200,
        json=_receipt_payload(),
    )
    monkeypatch.setattr(workspace, "_stdin_is_tty", lambda: True, raising=False)

    result = runner.invoke(
        app,
        [
            "workspace",
            "apply",
            "run-1",
            "--decision-id",
            "decision-1",
            "--token-stdin",
        ],
        input=f"{_TOKEN}\n",
    )

    assert result.exit_code == 1
    assert "hidden" in result.output.lower()
    assert _TOKEN not in result.output
    assert route.call_count == 0


def test_approve_apply_now_keeps_issued_token_in_process_only(
    respx_mock,
    caplog: pytest.LogCaptureFixture,
    tmp_path,
) -> None:
    decision = respx_mock.post(f"{_BASE}/api/workspace-runs/run-1/apply-decisions").respond(
        201,
        json={"success": True, "data": {"decision": {"id": "decision-1"}, "token": _TOKEN}},
    )
    apply = respx_mock.post(f"{_BASE}/api/workspace-runs/run-1/apply").respond(
        200,
        json=_receipt_payload(),
    )
    caplog.set_level(logging.DEBUG)

    result = runner.invoke(
        app,
        ["workspace", "approve", "run-1", "--reason", "reviewed", "--apply-now"],
    )

    assert result.exit_code == 0, result.output
    assert "receipt-1" in result.output
    assert _TOKEN not in result.output
    assert _TOKEN not in caplog.text
    assert json.loads(decision.calls[0].request.content) == {"reason": "reviewed"}
    assert json.loads(apply.calls[0].request.content) == {
        "decision_id": "decision-1",
        "token": _TOKEN,
    }
    for call in (*decision.calls, *apply.calls):
        assert _TOKEN not in str(call.request.url)
        assert _TOKEN not in "\n".join(
            f"{key}: {value}" for key, value in call.request.headers.items()
        )
    assert not (tmp_path / "credentials.json").exists()


def test_approve_exposes_only_required_apply_now_and_never_exports_a_token(respx_mock) -> None:
    route = respx_mock.post(f"{_BASE}/api/workspace-runs/run-1/apply-decisions").respond(
        201,
        json={"success": True, "data": {"decision": {"id": "decision-1"}, "token": _TOKEN}},
    )

    help_result = runner.invoke(app, ["workspace", "approve", "--help"])
    missing_apply_now = runner.invoke(
        app,
        ["workspace", "approve", "run-1", "--reason", "reviewed"],
    )
    rejected_export = runner.invoke(
        app,
        ["workspace", "approve", "run-1", "--reason", "reviewed", "--token-stdout"],
    )

    assert help_result.exit_code == 0, help_result.output
    assert "--apply-now" in help_result.output
    assert "--token-stdout" not in help_result.output
    assert missing_apply_now.exit_code == 2
    assert rejected_export.exit_code == 2
    assert _TOKEN not in missing_apply_now.output
    assert _TOKEN not in rejected_export.output
    assert route.call_count == 0


def test_workspace_commands_use_stable_failure_exit_code(respx_mock) -> None:
    respx_mock.get(f"{_BASE}/api/workspace-runs/missing/status").respond(
        404,
        json={"detail": "not found"},
    )

    result = runner.invoke(app, ["workspace", "status", "missing"])

    assert result.exit_code == 1
    assert "(404)" in result.output


@pytest.mark.parametrize(
    ("command", "method", "path"),
    [
        (["workspace", "status", "run-1"], "GET", "/api/workspace-runs/run-1/status"),
        (["workspace", "preview", "run-1"], "GET", "/api/workspace-runs/run-1/changes"),
        (
            ["workspace", "approve", "run-1", "--reason", "reviewed", "--apply-now"],
            "POST",
            "/api/workspace-runs/run-1/apply-decisions",
        ),
        (
            [
                "workspace",
                "apply",
                "run-1",
                "--decision-id",
                "decision-1",
                "--token-stdin",
            ],
            "POST",
            "/api/workspace-runs/run-1/apply",
        ),
    ],
    ids=("status", "preview", "approve", "apply"),
)
def test_workspace_command_transport_failures_use_exit_one_without_secret_leak(
    respx_mock,
    command: list[str],
    method: str,
    path: str,
) -> None:
    respx_mock.request(method, f"{_BASE}{path}").mock(
        side_effect=httpx.ConnectError(f"transport rejected {_TOKEN}")
    )

    result = runner.invoke(app, command, input=f"{_TOKEN}\n")

    assert result.exit_code == 1
    assert "transport unavailable" in result.output
    assert _TOKEN not in result.output


@pytest.mark.parametrize(
    ("command", "method", "path"),
    [
        (["workspace", "status", "run-1"], "GET", "/api/workspace-runs/run-1/status"),
        (["workspace", "preview", "run-1"], "GET", "/api/workspace-runs/run-1/changes"),
        (
            ["workspace", "approve", "run-1", "--reason", "reviewed", "--apply-now"],
            "POST",
            "/api/workspace-runs/run-1/apply-decisions",
        ),
        (
            [
                "workspace",
                "apply",
                "run-1",
                "--decision-id",
                "decision-1",
                "--token-stdin",
            ],
            "POST",
            "/api/workspace-runs/run-1/apply",
        ),
    ],
    ids=("status", "preview", "approve", "apply"),
)
def test_workspace_command_api_failures_use_exit_one_without_response_reflection(
    respx_mock,
    command: list[str],
    method: str,
    path: str,
) -> None:
    respx_mock.request(method, f"{_BASE}{path}").respond(
        409,
        json={"detail": {"code": "unsafe", "token": _TOKEN}},
    )

    result = runner.invoke(app, command, input=f"{_TOKEN}\n")

    assert result.exit_code == 1
    assert "(409)" in result.output
    assert _TOKEN not in result.output


@pytest.mark.parametrize(
    "command",
    [
        ["workspace", "status"],
        ["workspace", "preview"],
        ["workspace", "approve", "run-1", "--reason", "reviewed"],
        ["workspace", "apply", "run-1"],
    ],
    ids=("status", "preview", "approve", "apply"),
)
def test_workspace_command_validation_failures_use_exit_two(command: list[str]) -> None:
    result = runner.invoke(app, command)

    assert result.exit_code == 2
    assert _TOKEN not in result.output
