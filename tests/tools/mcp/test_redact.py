"""tianshu.tools.mcp.redact 单测。"""

from __future__ import annotations

import pytest

from tianshu.tools.mcp.redact import redact


@pytest.mark.unit
def test_redact_bearer_header() -> None:
    out = redact("Authorization: Bearer ghp_abcdefg1234567890XYZ")
    assert "ghp_abcdefg1234567890XYZ" not in out
    assert "[REDACTED]" in out


@pytest.mark.unit
def test_redact_basic_auth() -> None:
    out = redact("Basic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in out
    assert "[REDACTED]" in out


@pytest.mark.unit
def test_redact_github_pat() -> None:
    pat = "ghp_" + "x" * 40
    out = redact(f"Authentication failed with token {pat}")
    assert pat not in out


@pytest.mark.unit
def test_redact_query_string_token() -> None:
    out = redact("connect failed: api_key=verysecretvalue")
    assert "verysecretvalue" not in out
    assert "[REDACTED]" in out


@pytest.mark.unit
def test_redact_long_opaque_string() -> None:
    long = "a" * 64
    out = redact(f"server returned id={long}")
    assert long not in out


@pytest.mark.unit
def test_redact_preserves_short_text() -> None:
    out = redact("connect failed: timeout")
    assert out == "connect failed: timeout"


@pytest.mark.unit
def test_redact_empty_safe() -> None:
    assert redact("") == ""
