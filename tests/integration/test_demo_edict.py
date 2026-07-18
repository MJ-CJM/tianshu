"""G1.5：demo 档位下经正常 Agent 工具路径完成一个确定性 Edict。

进程内集成：真 Storage / ConfigManager(runtime demo) / ProviderManager(demo_mode)
/ ToolRegistry(register_builtins) / Agent。完整 HTTP + WorkspaceService 的
governed 黑盒断言（lease/restore/change 证据）属 S1.5 fresh-wheel 测试。
"""

from __future__ import annotations

import pytest

from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.config_manager import ConfigManager, LLMConfigState
from tianshu.executor.managed_tools import bind_managed_attempt_authority
from tianshu.models import Edict, TaskStatus
from tianshu.providers.demo import DEMO_MARK, DEMO_MODEL, demo_llm_config_state
from tianshu.providers.manager import ProviderManager
from tianshu.skills.loader import SkillsLoader
from tianshu.storage import Storage
from tianshu.tools.builtins import register_builtins
from tianshu.tools.registry import ToolRegistry


class _ManagedEffectPassthrough:
    async def execute(self, *, invoke, invocation_id, **_kwargs):
        assert invocation_id is not None
        return await invoke(invocation_id)


@pytest.fixture
def no_network(monkeypatch):
    import litellm

    async def _forbidden(**kwargs):  # pragma: no cover
        raise AssertionError("litellm must not be called in demo mode")

    monkeypatch.setattr(litellm, "acompletion", _forbidden)


async def test_governed_demo_edict_uses_normal_agent_tool_path(tmp_path, no_network):
    from tianshu.executor.agent import Agent

    storage = Storage(str(tmp_path / "db.sqlite3"))
    storage.init_db()
    workspace = tmp_path / "工作 区"  # 非 ASCII + 空格路径
    workspace.mkdir()

    live_seed = LLMConfigState(name="live-main", model="gpt-4o-mini", api_key="k", api_base="")
    config_manager = ConfigManager(
        live_seed, storage=storage, runtime_override=demo_llm_config_state()
    )
    provider_manager = ProviderManager(
        storage=storage, config_manager=config_manager, demo_mode=True
    )
    # sync_all 在 demo 下是 no-op：providers 表零行
    provider_manager.sync_all()
    assert storage.list_providers() == []

    registry = ToolRegistry()
    register_builtins(registry, workspace_dir=str(workspace), storage=storage)
    registry.set_managed_effect_executor(_ManagedEffectPassthrough())
    skills = SkillsLoader(builtin_dir=tmp_path / "skills", char_budget=1000)

    agent = Agent(
        config_manager=config_manager,
        tools=registry,
        skills=skills,
        provider_manager=provider_manager,
    )
    edict = Edict(goal="write the demo artifact")
    authority = AttemptAuthority(
        attempt_id="attempt-demo",
        memorial_id="memorial-demo",
        owner_id="worker-demo",
        fencing_token=1,
    )
    with bind_managed_attempt_authority(authority):
        memorial = await agent.execute(edict)

    assert memorial.status == TaskStatus.COMPLETED
    assert DEMO_MARK in (memorial.result or "")
    assert memorial.usage.total_tokens == 0
    assert memorial.usage.cost_cny == 0.0
    assert memorial.usage.actual_model == DEMO_MODEL

    artifact = workspace / "DEMO.md"
    assert artifact.is_file(), "write_file 应经正常工具路径落盘"
    assert DEMO_MARK in artifact.read_text(encoding="utf-8")
    storage.close()
