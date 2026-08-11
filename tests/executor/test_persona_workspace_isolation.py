"""工作区按官员隔离（issue #33）。

分层覆盖：

1. 解析序 —— resolve_workspace_root：lease > 官员 override > 进程默认
2. 执行层 —— registry.execute + 真实工具：相对路径落官员工作区、
   跨官员工作区被拒、allowed_paths 仍可显式放行
3. 全链 —— mock LLM 驱动真实 Agent + PolicyHook：T1 写入落官员工作区，
   越界写主工作区被 WorkspaceBoundaryRule 拒

安全底线：未配置 workspace_dir 的官员行为与本机制引入前逐字节一致。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tianshu.executor.agent import Agent, _ambient_tool_context
from tianshu.executor.policy_hook import PolicyHook
from tianshu.executor.workspace_context import (
    bind_workspace_root_override,
    resolve_workspace_root,
)
from tianshu.kernel.hooks import HookRegistry, HookType
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.persona.model import AgentPersona
from tianshu.skills.loader import SkillsLoader
from tianshu.tools.builtins import register_builtins
from tianshu.tools.policy import PolicyEngine
from tianshu.tools.policy_rules import build_default_rules
from tianshu.tools.registry import ToolRegistry


def _persona(**kw) -> AgentPersona:
    base = {
        "id": "smg",
        "name": "司马光",
        "department": "wenyuan",
        "soul_path": "/tmp/p/SOUL.md",
        "role_path": "/tmp/p/ROLE.md",
        "memory_path": "/tmp/p/MEMORY.md",
    }
    return AgentPersona(**{**base, **kw})


class TestResolveOrder:
    def test_default_when_nothing_bound(self, tmp_path: Path):
        assert resolve_workspace_root(tmp_path) == tmp_path.resolve()

    def test_override_beats_default(self, tmp_path: Path):
        ws2 = tmp_path / "ws2"
        ws2.mkdir()
        with bind_workspace_root_override(ws2):
            assert resolve_workspace_root(tmp_path) == ws2.resolve()
        assert resolve_workspace_root(tmp_path) == tmp_path.resolve()

    def test_lease_binding_beats_override(self, tmp_path: Path, monkeypatch):
        """治理 apply 的隔离暂存必须最优先——override 遮蔽 lease 会把
        staging 里的改动写去错误位置。"""
        lease_root = (tmp_path / "staging").resolve()
        monkeypatch.setattr(
            "tianshu.executor.workspace_context.validate_current_workspace_binding",
            lambda: SimpleNamespace(root=lease_root),
        )
        with bind_workspace_root_override(tmp_path / "ws2"):
            assert resolve_workspace_root(tmp_path) == lease_root


class TestAmbientToolContext:
    """agent 的绑定 helper：persona.workspace_dir 非空才绑 override。"""

    def test_binds_override_for_dedicated_workspace(self, tmp_path: Path):
        ws2 = tmp_path / "ws2"
        ws2.mkdir()
        with _ambient_tool_context(Edict(goal="x"), _persona(workspace_dir=str(ws2))):
            assert resolve_workspace_root(tmp_path) == ws2.resolve()

    def test_no_override_without_workspace_dir(self, tmp_path: Path):
        with _ambient_tool_context(Edict(goal="x"), _persona()):
            assert resolve_workspace_root(tmp_path) == tmp_path.resolve()

    def test_no_override_without_persona(self, tmp_path: Path):
        with _ambient_tool_context(Edict(goal="x"), None):
            assert resolve_workspace_root(tmp_path) == tmp_path.resolve()


class TestRegistryExecutionLayer:
    """T0 工具走 registry 快路径——官员工作区必须在这一层就生效。"""

    def _registry(self, main_ws: Path) -> ToolRegistry:
        registry = ToolRegistry()
        register_builtins(registry, workspace_dir=str(main_ws))
        return registry

    async def test_relative_path_lands_in_dedicated_workspace(self, tmp_path: Path):
        main = tmp_path / "main"
        ws2 = tmp_path / "ws2"
        main.mkdir()
        ws2.mkdir()
        (main / "report.md").write_text("主工作区", encoding="utf-8")
        (ws2 / "report.md").write_text("官员专属", encoding="utf-8")
        registry = self._registry(main)

        with _ambient_tool_context(Edict(goal="x"), _persona(workspace_dir=str(ws2))):
            result = await registry.execute("read_file", {"path": "report.md"})
        assert not result.is_error
        assert "官员专属" in result.content

    async def test_main_workspace_becomes_outside(self, tmp_path: Path):
        """隔离语义：配了专属工作区后，主工作区就是界外。"""
        main = tmp_path / "main"
        ws2 = tmp_path / "ws2"
        main.mkdir()
        ws2.mkdir()
        (main / "secret.md").write_text("x", encoding="utf-8")
        registry = self._registry(main)

        with _ambient_tool_context(Edict(goal="x"), _persona(workspace_dir=str(ws2))):
            result = await registry.execute("read_file", {"path": str(main / "secret.md")})
        assert result.is_error
        assert "outside workspace" in result.content

    async def test_cross_official_workspace_denied(self, tmp_path: Path):
        ws_a = tmp_path / "ws-a"
        ws_b = tmp_path / "ws-b"
        ws_a.mkdir()
        ws_b.mkdir()
        (ws_b / "draft.md").write_text("乙官文书", encoding="utf-8")
        registry = self._registry(tmp_path / "main")

        with _ambient_tool_context(Edict(goal="x"), _persona(workspace_dir=str(ws_a))):
            result = await registry.execute("read_file", {"path": str(ws_b / "draft.md")})
        assert result.is_error, "甲官不得读乙官工作区"

    async def test_allowed_paths_still_grants_outside(self, tmp_path: Path):
        """#35 的事前授权与专属工作区可组合：白名单仍可放行界外。"""
        ws_a = tmp_path / "ws-a"
        shared = tmp_path / "shared"
        ws_a.mkdir()
        shared.mkdir()
        (shared / "report.md").write_text("共享报表", encoding="utf-8")
        registry = self._registry(tmp_path / "main")
        persona = _persona(workspace_dir=str(ws_a), allowed_paths=[f"{shared.resolve()}/**"])
        edict = Edict(goal="x")
        from tianshu.models.edict import PolicyProfilePayload

        edict.runtime.policy_profile = PolicyProfilePayload(
            allowed_paths=list(persona.allowed_paths)
        )

        with _ambient_tool_context(edict, persona):
            result = await registry.execute("read_file", {"path": str(shared / "report.md")})
        assert not result.is_error
        assert "共享报表" in result.content

    async def test_no_workspace_dir_byte_identical(self, tmp_path: Path):
        main = tmp_path / "main"
        main.mkdir()
        (main / "a.md").write_text("主", encoding="utf-8")
        registry = self._registry(main)

        with _ambient_tool_context(Edict(goal="x"), _persona()):
            result = await registry.execute("read_file", {"path": "a.md"})
        assert not result.is_error and "主" in result.content


class TestFullChainThroughAgent:
    """mock LLM → Agent → PolicyHook（真规则链）→ T1 写入按官员工作区判界。"""

    def _agent_and_tools(self, config_manager, main_ws: Path, storage):
        tools = ToolRegistry()
        register_builtins(tools, workspace_dir=str(main_ws))
        policy_hook = PolicyHook(
            engine=PolicyEngine(rules=build_default_rules()),
            workspace_root=main_ws,
            storage=storage,
            tool_registry=tools,
        )
        hooks = HookRegistry()
        hooks.register(HookType.BEFORE_TOOL_CALL, policy_hook.on_before_tool_call)
        agent = Agent(
            config_manager=config_manager,
            tools=tools,
            skills=SkillsLoader(builtin_dir=main_ws, char_budget=0),
            hook_registry=hooks,
        )
        return agent, tools

    def _responses(self, path: str):
        # 用 edit_file（T1、无 managed 语义）：write_file 声明 provider_idempotent，
        # 无 managed authority 时 registry 直接拒，测不到工作区边界。
        return [
            MagicMock(
                content="edit it",
                reasoning_content=None,
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "edit_file",
                        "args": json.dumps({"path": path, "old_text": "草稿", "new_text": "奏折"}),
                    }
                ],
                usage=UsageSummary(),
                finish_reason="tool_calls",
            ),
            MagicMock(
                content="done",
                reasoning_content=None,
                tool_calls=None,
                usage=UsageSummary(),
                finish_reason="stop",
            ),
        ]

    async def test_relative_write_lands_in_dedicated_workspace(
        self, config_manager, tmp_path, storage
    ):
        main = tmp_path / "main"
        ws2 = tmp_path / "ws2"
        main.mkdir()
        ws2.mkdir()
        (main / "memo.md").write_text("草稿", encoding="utf-8")
        (ws2 / "memo.md").write_text("草稿", encoding="utf-8")
        agent, _ = self._agent_and_tools(config_manager, main, storage)
        edict = Edict(goal="write in dedicated ws")
        edict.runtime.max_iterations = 2
        memorial = Memorial(edict_id=edict.id, status=TaskStatus.RUNNING)
        storage.save_edict(edict)
        storage.save_memorial(memorial)

        with patch("tianshu.executor.agent.LLMClient") as mock_client:
            mock_client.return_value = AsyncMock(
                chat=AsyncMock(side_effect=self._responses("memo.md"))
            )
            result = await agent.execute(
                edict, memorial=memorial, persona=_persona(workspace_dir=str(ws2))
            )

        assert result.status is TaskStatus.COMPLETED
        assert (ws2 / "memo.md").read_text(encoding="utf-8") == "奏折", (
            "相对路径写入须落官员专属工作区"
        )
        assert (main / "memo.md").read_text(encoding="utf-8") == "草稿", "主工作区不得被动"

    async def test_write_into_main_workspace_denied_by_boundary_rule(
        self, config_manager, tmp_path, storage
    ):
        main = tmp_path / "main"
        ws2 = tmp_path / "ws2"
        main.mkdir()
        ws2.mkdir()
        (main / "sneak.md").write_text("草稿", encoding="utf-8")
        agent, _ = self._agent_and_tools(config_manager, main, storage)
        edict = Edict(goal="attempt main ws write")
        edict.runtime.max_iterations = 2
        memorial = Memorial(edict_id=edict.id, status=TaskStatus.RUNNING)
        storage.save_edict(edict)
        storage.save_memorial(memorial)

        with patch("tianshu.executor.agent.LLMClient") as mock_client:
            mock_client.return_value = AsyncMock(
                chat=AsyncMock(side_effect=self._responses(str(main / "sneak.md")))
            )
            await agent.execute(edict, memorial=memorial, persona=_persona(workspace_dir=str(ws2)))

        assert (main / "sneak.md").read_text(encoding="utf-8") == "草稿", "越界写入必须被拦"
        events = storage._conn.execute(  # noqa: SLF001
            "SELECT payload_json FROM events WHERE event_type = 'policy.decision'"
        ).fetchall()
        decisions = [json.loads(row[0]) for row in events]
        assert any(
            d["rule_id"] == "workspace_boundary" and d["verdict"] == "deny" for d in decisions
        ), decisions
