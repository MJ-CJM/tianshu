"""LSP 诊断(迭代 5「执行 2.0」)——basedpyright JSON 解析 + 优雅降级。"""

from __future__ import annotations

import json

from tianshu.executor import execution_gateway as process_boundary
from tianshu.lsp.diagnostics import (
    DiagnosticOutcome,
    format_diagnostics,
    is_enabled,
    parse_diagnostics,
    run_diagnostics,
)

_SAMPLE = json.dumps(
    {
        "generalDiagnostics": [
            {
                "severity": "error",
                "message": '"x" is not defined',
                "rule": "reportUndefinedVariable",
                "range": {"start": {"line": 4, "character": 0}},
            },
            {
                "severity": "warning",
                "message": "unused import",
                "range": {"start": {"line": 0, "character": 0}},
            },
            {"severity": "information", "message": "noise", "range": {"start": {"line": 1}}},
        ]
    }
)


class TestParse:
    def test_extracts_errors_and_warnings_only(self):
        diags = parse_diagnostics(_SAMPLE)
        assert len(diags) == 2  # information 被过滤
        assert diags[0]["severity"] == "error"
        assert diags[0]["line"] == 5  # 0-based → 1-based
        assert diags[0]["rule"] == "reportUndefinedVariable"
        assert diags[1]["line"] == 1

    def test_bad_json_returns_empty(self):
        assert parse_diagnostics("not json{") == []
        assert parse_diagnostics("") == []

    def test_no_diagnostics_key(self):
        assert parse_diagnostics('{"other": 1}') == []


class TestFormat:
    def test_format_readable(self):
        diags = parse_diagnostics(_SAMPLE)
        text = format_diagnostics(diags)
        assert "basedpyright 诊断(2 条" in text
        assert "L5 error" in text and "reportUndefinedVariable" in text

    def test_empty_format(self):
        assert format_diagnostics([]) == ""


class TestGracefulDegrade:
    def test_disabled_returns_structured_outcome(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TIANSHU_LSP_ENABLED", raising=False)
        assert not is_enabled()
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        outcome = run_diagnostics(f)
        assert isinstance(outcome, DiagnosticOutcome)
        assert outcome.status == "disabled"
        assert outcome.diagnostics == ()
        assert outcome.advisory is None
        assert outcome.correlation_id

    def test_non_python_is_not_applicable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TIANSHU_LSP_ENABLED", "on")
        f = tmp_path / "a.txt"
        f.write_text("hello")
        outcome = run_diagnostics(f)
        assert outcome.status == "not_applicable"
        assert outcome.diagnostics == ()
        assert outcome.advisory is None

    def test_enabled_but_no_basedpyright_returns_advisory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TIANSHU_LSP_ENABLED", "on")
        monkeypatch.setattr(
            process_boundary,
            "resolve_system_adapter_executable",
            lambda _adapter, workspace_root: None,
        )
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        outcome = run_diagnostics(f)
        assert outcome.status == "unavailable"
        assert outcome.diagnostics == ()
        assert outcome.advisory
        assert outcome.correlation_id
