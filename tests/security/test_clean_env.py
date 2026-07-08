"""子进程 clean-env 白名单(迭代 3「深防御」)——secret 不经 env 泄漏。"""

from __future__ import annotations

from tianshu.security.clean_env import SAFE_ENV_VARS, build_clean_env


class TestBuildCleanEnv:
    def test_secrets_stripped(self):
        env = build_clean_env(
            base_env={
                "PATH": "/bin",
                "HOME": "/h",
                "TIANSHU_LLM_API_KEY": "sk-secret",
                "TIANSHU_SECRET_MASTER_KEY": "masterkey",
                "AWS_SECRET_ACCESS_KEY": "aws",
            }
        )
        assert env == {"PATH": "/bin", "HOME": "/h"}
        assert "TIANSHU_LLM_API_KEY" not in env
        assert "TIANSHU_SECRET_MASTER_KEY" not in env

    def test_safe_vars_passthrough(self):
        base = {v: f"val-{v}" for v in SAFE_ENV_VARS}
        env = build_clean_env(base_env=base)
        assert env == base

    def test_explicit_passthrough_allowed(self):
        env = build_clean_env(
            "MY_VAR,ANOTHER", base_env={"PATH": "/bin", "MY_VAR": "1", "ANOTHER": "2", "X": "3"}
        )
        assert env["MY_VAR"] == "1" and env["ANOTHER"] == "2"
        assert "X" not in env

    def test_invalid_name_rejected(self):
        # 防注入:非法变量名(含空格/等号)不透传
        env = build_clean_env(
            "BAD NAME,OK_VAR=x,GOOD", base_env={"PATH": "/bin", "GOOD": "g", "OK_VAR": "y"}
        )
        assert env.get("GOOD") == "g"
        assert "BAD NAME" not in env
        assert "OK_VAR" not in env  # "OK_VAR=x" 整体作为名字非法

    def test_missing_var_skipped(self):
        env = build_clean_env("ABSENT", base_env={"PATH": "/bin"})
        assert "ABSENT" not in env

    def test_passthrough_from_env_var(self):
        env = build_clean_env(
            base_env={
                "PATH": "/bin",
                "TIANSHU_SHELL_ENV_PASSTHROUGH": "PROXY_URL",
                "PROXY_URL": "http://p",
            }
        )
        assert env["PROXY_URL"] == "http://p"
