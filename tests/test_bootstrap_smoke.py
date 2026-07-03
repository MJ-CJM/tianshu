"""装配冒烟测试——app.state 服务键全量断言（拆分行为锚点）。

`tianshu.app.lifespan()` 把 ~50 个服务对象挂到 `app.state` 上。本测试在
lifespan 从单体函数拆分为 `bootstrap/` wiring 函数序列（B2-T4）之前先跑绿，
锁定"哪些键必须存在、必须非 None"这条行为基线；拆分之后再跑一次必须仍然绿，
证明装配顺序与最终状态未变。

key 清单来自对拆分前 app.py 的全量 grep：`app.state\\.[a-zA-Z_]* =`。
"""

from __future__ import annotations

import pytest

from tianshu.app import create_app, lifespan

# 全部在 lifespan() 中被赋值、且默认测试环境下保证非 None 的 app.state 键。
NON_NULLABLE_STATE_KEYS = [
    "storage",
    "event_bus",
    "hook_registry",
    "mcp_manager",
    "_mcp_start_task",
    "persona_loader",
    "runtime_personas_dir",
    "template_library",
    "drawer_store",
    "memory_config",
    "config_manager",
    "provider_manager",
    "agent",
    "skills_loader",
    "skill_metrics_store",
    "tool_registry",
    "prompt_builder",
    "personas_dir",
    "worker_pool",
    "lane_manager",
    "auditor",
    "channel_registry",
    "notifier",
    "session_rule_store",
    "executor",
    "dag_scheduler",
    "approval_manager",
    "cost_manager",
    "bot_manager",
    "policy_engine",
    "policy_hook",
    "memory_manager",
    "consultation",
    "orchestrator_ctx",
    "evaluator",
    "official_selector",
    "planner",
    "scheduler",
    "plugin_api",
    "profile_synthesizer",
    "profile_trigger",
    "skill_curator",
    "universe_manager",
    "code_deployer",
    "universe_evolver",
    "digest_generator",
    "_digest_task",
    "running_tasks",
]

# 按设计允许为 None：默认测试环境未配置 feishu/telegram，
# ChannelBotManager.get() 对未注册的实例返回 None（见 bot_manager.py）。
NULLABLE_STATE_KEYS = [
    "feishu_bot",
    "telegram_bot",
]

ALL_STATE_KEYS = [*NON_NULLABLE_STATE_KEYS, *NULLABLE_STATE_KEYS]


@pytest.fixture
async def booted_app():
    app = create_app()
    async with lifespan(app):
        yield app


class TestBootstrapSmoke:
    async def test_no_missing_state_keys(self, booted_app):
        missing = [key for key in ALL_STATE_KEYS if not hasattr(booted_app.state, key)]
        assert not missing, f"app.state 缺少以下 key: {missing}"

    @pytest.mark.parametrize("key", NON_NULLABLE_STATE_KEYS)
    async def test_state_key_is_not_none(self, booted_app, key):
        assert getattr(booted_app.state, key) is not None, f"app.state.{key} 不应为 None"

    @pytest.mark.parametrize("key", NULLABLE_STATE_KEYS)
    async def test_nullable_state_key_present(self, booted_app, key):
        assert hasattr(booted_app.state, key), f"app.state.{key} 应存在（值可为 None）"
