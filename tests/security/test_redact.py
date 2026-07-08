"""出站脱敏红队用例(迭代 3「深防御」)——各类 secret 泄漏样本全拦截。"""

from __future__ import annotations

import pytest

from tianshu.security.redact import redact_mapping, redact_text


class TestRedactText:
    @pytest.mark.parametrize(
        "raw,marker",
        [
            ("key sk-abcdefghij0123456789xyz", "[REDACTED API KEY]"),
            ("AKIAIOSFODNN7EXAMPLE here", "[REDACTED AWS KEY]"),
            ("ghp_" + "a" * 36, "[REDACTED GITHUB TOKEN]"),
            ("glpat-" + "x" * 20, "[REDACTED GITLAB TOKEN]"),
            ("xoxb-123456-abcdefghijkl", "[REDACTED SLACK TOKEN]"),
            ("Authorization: Bearer eyJabc.def.ghi", "Bearer [REDACTED]"),
            ("token eyJhbGci0123.eyJzdWI0123.SflKxwRJ01", "[REDACTED JWT]"),
            ("postgres://u:p@host:5432/db", "[REDACTED CONNECTION STRING]"),
            ("API_KEY=supersecretvalue123", "[REDACTED CREDENTIAL]"),
            ("DATABASE_URL=postgres://x", "[REDACTED CREDENTIAL]"),
        ],
    )
    def test_secret_patterns_redacted(self, raw, marker):
        assert marker in redact_text(raw)

    def test_pem_private_key_multiline(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\nDEF123\n-----END RSA PRIVATE KEY-----"
        assert redact_text(pem) == "[REDACTED PRIVATE KEY]"

    def test_home_dir_username_masked(self):
        from tianshu.security import redact as r

        if r._HOME and r._USERNAME:
            out = redact_text(f"error at {r._HOME}/project/file.py")
            assert r._USERNAME not in out

    def test_clean_text_untouched(self):
        assert redact_text("just a normal log line, no secrets") == (
            "just a normal log line, no secrets"
        )

    def test_empty(self):
        assert redact_text("") == ""


class TestRedactMapping:
    def test_nested_and_type_preservation(self):
        out = redact_mapping(
            {
                "token": "sk-abcdefghij0123456789xyz",
                "count": 42,
                "nested": {"key": "ghp_" + "z" * 36},
                "list": ["Bearer eyJx.y.z", "clean"],
            }
        )
        assert out["token"] == "[REDACTED API KEY]"
        assert out["count"] == 42
        assert out["nested"]["key"] == "[REDACTED GITHUB TOKEN]"
        assert out["list"][0] == "Bearer [REDACTED]"
        assert out["list"][1] == "clean"
