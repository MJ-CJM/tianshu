"""tianshu-guard 配置(Python 单一真相)+ PolicyCompiler 测试。

guard 运行时(TS)本身 Python 测不到,但配置的编译语义(tighten-only 不变量、
序列化、bash 规则下发)是 Python 单一真相,在此锁死。"""

import json

from tianshu.executor.keqing.guard_config import (
    GUARD_CONFIG_VERSION,
    BashRules,
    GuardConfig,
    PolicyInputs,
    compile_guard_config,
)


class TestGuardConfigSerialization:
    def test_to_guard_json_roundtrips(self):
        cfg = GuardConfig(
            tool_deny=("web_fetch",),
            bash=BashRules(deny_segments=("rm -rf *",), allow_exact=("git status",)),
            provider_allowlist=("anthropic",),
            gateway_url="https://gw/api/keqing/llm",
            edict_id="e1",
            run_id="r1",
        )
        data = json.loads(cfg.to_guard_json())
        assert data["version"] == GUARD_CONFIG_VERSION
        assert data["project_trust"] == "no"
        assert data["tool_deny"] == ["web_fetch"]
        assert data["bash"]["deny_segments"] == ["rm -rf *"]
        assert data["bash"]["allow_exact"] == ["git status"]
        assert data["gateway_url"] == "https://gw/api/keqing/llm"
        assert data["handshake_required"] is True


class TestPolicyCompiler:
    def test_compiles_inputs_to_config(self):
        cfg = compile_guard_config(
            PolicyInputs(
                edict_id="e",
                run_id="r",
                deny_tools=("web_fetch",),
                ask_tools=("write",),
                bash_deny=("rm -rf *", "sudo *"),
                bash_allow=("pytest", "git status"),
                provider_allowlist=("anthropic",),
                model_allowlist=("anthropic/opus",),
                gateway_url="https://gw",
            )
        )
        assert cfg.tool_deny == ("web_fetch",)
        assert cfg.tool_ask == ("write",)
        assert cfg.bash.deny_segments == ("rm -rf *", "sudo *")
        assert cfg.bash.allow_exact == ("pytest", "git status")
        assert cfg.provider_allowlist == ("anthropic",)
        assert cfg.gateway_url == "https://gw"

    def test_project_trust_always_no_tighten_only(self):
        # tighten-only 不变量:project_trust 恒为 no,不可被放宽
        cfg = compile_guard_config(PolicyInputs(edict_id="e", run_id="r"))
        assert cfg.project_trust == "no"

    def test_empty_provider_allowlist_is_fail_closed(self):
        # 空 provider 白名单 → 客卿无可用出口(fail-closed),不静默放行
        cfg = compile_guard_config(PolicyInputs(edict_id="e", run_id="r"))
        assert cfg.provider_allowlist == ()
        assert cfg.handshake_required is True

    def test_bash_unsplittable_defaults_to_ask(self):
        cfg = compile_guard_config(PolicyInputs(edict_id="e", run_id="r"))
        assert cfg.bash.unsplittable_action == "ask"
