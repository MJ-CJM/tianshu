"""官员工具 ACL 从声明变为强制（issue #40）。

三层覆盖，缺一不可（#35 的教训：只验判定层会"单测全绿但功能不生效"）：

1. 语义层 —— persona_tool_verdict 与 persona_can_use 逐字等价
2. 判定层 —— PersonaToolRule 的条款→verdict 映射与引擎短路顺序
3. 执行层 —— registry.execute 对 T0 快路径的名单兜底（真实工具真实调用）

安全底线：无 persona 上下文时全部弃权，行为与本机制引入前逐字节一致。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianshu.kernel.ambient import bind_persona
from tianshu.models.edict import Edict
from tianshu.persona.match import persona_can_use, persona_tool_verdict
from tianshu.persona.model import AgentPersona
from tianshu.tools.policy import PolicyContext, PolicyEngine
from tianshu.tools.policy_rules import build_default_rules
from tianshu.tools.policy_rules.persona_tool import PersonaTierRule, PersonaToolAclRule
from tianshu.tools.types import ToolTier


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


def _ctx(tool_name: str, tier: ToolTier, workspace: Path | None = None) -> PolicyContext:
    return PolicyContext(
        tool_name=tool_name,
        tool_tier=tier,
        args={},
        edict=Edict(goal="x"),
        memorial=None,
        workspace_root=workspace or Path("/tmp/ws"),
        iteration=0,
    )


class TestVerdictSemantics:
    """persona_tool_verdict 是唯一语义源；persona_can_use 必须与其逐字等价。"""

    CASES = [
        # (tools_allowed, tools_denied, tier_max, tool, tier, expected_clause)
        ([], [], 4, "shell_exec", 4, None),
        ([], ["shell_exec"], 4, "shell_exec", 4, "denied"),
        ([], ["mcp_github_*"], 4, "mcp_github_create_pr", 3, "denied"),
        (["read_file"], [], 4, "read_file", 0, None),
        (["read_file"], [], 4, "shell_exec", 4, "not_allowed"),
        (["read_file"], [], 0, "read_file", 0, None),  # allow 命中不再看 tier
        ([], [], 1, "edit_file", 1, None),
        ([], [], 1, "shell_exec", 4, "tier_exceeded"),
        (["read_*"], ["read_secret"], 4, "read_secret", 0, "denied"),  # deny 先于 allow
    ]

    @pytest.mark.parametrize("allowed,denied,tier_max,tool,tier,clause", CASES)
    def test_verdict_clause(self, allowed, denied, tier_max, tool, tier, clause):
        p = _persona(tools_allowed=allowed, tools_denied=denied, tool_tier_max=tier_max)
        assert persona_tool_verdict(p, tool, tier) == clause

    @pytest.mark.parametrize("allowed,denied,tier_max,tool,tier,clause", CASES)
    def test_can_use_equivalence(self, allowed, denied, tier_max, tool, tier, clause):
        """skills_api 走 persona_can_use——重构后语义不得漂移。"""
        p = _persona(tools_allowed=allowed, tools_denied=denied, tool_tier_max=tier_max)
        assert persona_can_use(p, tool, tier) is (clause is None)


class TestPersonaToolRule:
    @pytest.mark.asyncio
    async def test_no_persona_abstains(self):
        assert (
            await PersonaToolAclRule().evaluate(_ctx("shell_exec", ToolTier.T4_DANGEROUS)) is None
        )
        assert await PersonaTierRule().evaluate(_ctx("shell_exec", ToolTier.T4_DANGEROUS)) is None

    @pytest.mark.asyncio
    async def test_denied_tool_is_hard_deny(self):
        with bind_persona(_persona(tools_denied=["shell_exec"])):
            decision = await PersonaToolAclRule().evaluate(
                _ctx("shell_exec", ToolTier.T4_DANGEROUS)
            )
        assert decision is not None and decision.verdict == "deny"
        assert decision.metadata["clause"] == "denied"

    @pytest.mark.asyncio
    async def test_allowlist_miss_is_hard_deny(self):
        with bind_persona(_persona(tools_allowed=["read_file"])):
            decision = await PersonaToolAclRule().evaluate(_ctx("edit_file", ToolTier.T1_WORKSPACE))
        assert decision is not None and decision.verdict == "deny"
        assert decision.metadata["clause"] == "not_allowed"

    @pytest.mark.asyncio
    async def test_acl_rule_abstains_on_tier_clause(self):
        """名单规则不管 tier——越级交给低优先级的 PersonaTierRule。"""
        with bind_persona(_persona(tool_tier_max=1)):
            assert (
                await PersonaToolAclRule().evaluate(_ctx("shell_exec", ToolTier.T4_DANGEROUS))
                is None
            )

    @pytest.mark.asyncio
    async def test_tier_exceeded_requires_approval(self):
        """越级不是硬拒——奏请批准，与 bash_safety 的审批 UX 一致。"""
        with bind_persona(_persona(tool_tier_max=1)):
            decision = await PersonaTierRule().evaluate(_ctx("shell_exec", ToolTier.T4_DANGEROUS))
        assert decision is not None and decision.verdict == "require_approval"
        assert decision.metadata["clause"] == "tier_exceeded"

    @pytest.mark.asyncio
    async def test_tier_rule_abstains_on_name_clauses(self):
        """tier 规则不管名单条款——denied/not_allowed 由 PersonaToolAclRule 硬拒。"""
        with bind_persona(_persona(tools_denied=["shell_exec"])):
            assert (
                await PersonaTierRule().evaluate(_ctx("shell_exec", ToolTier.T4_DANGEROUS)) is None
            )

    @pytest.mark.asyncio
    async def test_pass_abstains_not_allows(self):
        """放行是弃权而非 allow 短路——工作区/网络等其他规则仍须评估。"""
        with bind_persona(_persona()):
            assert (
                await PersonaToolAclRule().evaluate(_ctx("read_file", ToolTier.T0_READONLY)) is None
            )


class TestEngineOrdering:
    @pytest.mark.asyncio
    async def test_denied_tool_never_reaches_approval(self):
        """引擎按 priority 短路：官员被禁的工具必须拿到 deny，
        而不是先被 bash_safety(80) 拦成 require_approval 再等人批。"""
        engine = PolicyEngine(rules=build_default_rules())
        ctx = PolicyContext(
            tool_name="shell_exec",
            tool_tier=ToolTier.T4_DANGEROUS,
            args={"command": "rm -rf /tmp/x"},
            edict=Edict(goal="x"),
            memorial=None,
            workspace_root=Path("/tmp/ws"),
            iteration=0,
        )
        with bind_persona(_persona(tools_denied=["shell_exec"])):
            decision = await engine.evaluate(ctx)
        assert decision.verdict == "deny"
        assert decision.rule_id == "persona_tool_acl"

    @pytest.mark.asyncio
    async def test_tier_exceeded_does_not_shortcircuit_bash_deny(self):
        """回归钉（审查 #4/#6）：tier 越级奏请不得抢在 bash_safety 硬 deny 前面。

        tier_max=2 的官员调 shell_exec 执行黑名单命令——若 PersonaTierRule 排在
        bash_safety(80) 之前拿到 require_approval，无条件硬拒的命令就变成一键
        可批。拆规则后 persona_tier 排在 15，bash 的 deny 必须先赢。"""
        engine = PolicyEngine(rules=build_default_rules())
        ctx = PolicyContext(
            tool_name="shell_exec",
            tool_tier=ToolTier.T4_DANGEROUS,
            args={"command": "sudo rm -rf /"},
            edict=Edict(goal="x"),
            memorial=None,
            workspace_root=Path("/tmp/ws"),
            iteration=0,
        )
        with bind_persona(_persona(tool_tier_max=2)):
            decision = await engine.evaluate(ctx)
        assert decision.verdict == "deny", decision
        assert decision.rule_id == "bash_safety", (
            f"黑名单命令必须被 bash_safety 硬拒，而不是被 persona 越级奏请短路：{decision.rule_id}"
        )

    @pytest.mark.asyncio
    async def test_tier_exceeded_surfaces_when_safe_rules_abstain(self):
        """安全规则都放行时，越级奏请仍兜底（tier 规则没被误删优先级）。"""
        engine = PolicyEngine(rules=build_default_rules())
        ctx = PolicyContext(
            tool_name="mcp_custom_tool",
            tool_tier=ToolTier.T3_WRITE,
            args={},
            edict=Edict(goal="x"),
            memorial=None,
            workspace_root=Path("/tmp/ws"),
            iteration=0,
        )
        with bind_persona(_persona(tool_tier_max=1)):
            decision = await engine.evaluate(ctx)
        assert decision.verdict == "require_approval"
        assert decision.rule_id == "persona_tier"

    @pytest.mark.asyncio
    async def test_unconstrained_persona_keeps_existing_verdicts(self):
        """迁移后（tier_max=4、空名单）官员的裁定与无 persona 时逐字一致。"""
        engine = PolicyEngine(rules=build_default_rules())
        ctx = PolicyContext(
            tool_name="shell_exec",
            tool_tier=ToolTier.T4_DANGEROUS,
            args={"command": "ls"},
            edict=Edict(goal="x"),
            memorial=None,
            workspace_root=Path("/tmp/ws"),
            iteration=0,
        )
        baseline = await engine.evaluate(ctx)
        with bind_persona(_persona(tool_tier_max=4)):
            with_persona = await engine.evaluate(ctx)
        assert (with_persona.verdict, with_persona.rule_id) == (
            baseline.verdict,
            baseline.rule_id,
        )


class TestRegistryExecutionLayer:
    """T0 工具在 agent 层走快路径绕过 hook chain——registry 必须自己拦名单条款。"""

    def _registry(self, tmp_path: Path):
        from tianshu.tools.builtins import register_builtins
        from tianshu.tools.registry import ToolRegistry

        registry = ToolRegistry()
        register_builtins(registry, workspace_dir=str(tmp_path))
        return registry

    @pytest.mark.asyncio
    async def test_denied_t0_tool_rejected_at_registry(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        registry = self._registry(tmp_path)

        with bind_persona(_persona(tools_denied=["read_file"])):
            result = await registry.execute("read_file", {"path": "a.txt"})
        assert result.is_error
        assert "职权契约" in result.content

    @pytest.mark.asyncio
    async def test_allowlist_scopes_t0_tools_too(self, tmp_path: Path):
        registry = self._registry(tmp_path)

        with bind_persona(_persona(tools_allowed=["read_file"])):
            result = await registry.execute("list_dir", {"path": "."})
        assert result.is_error
        assert "职权契约" in result.content

    @pytest.mark.asyncio
    async def test_allowed_t0_tool_still_works(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("内容", encoding="utf-8")
        registry = self._registry(tmp_path)

        with bind_persona(_persona(tools_allowed=["read_file"])):
            result = await registry.execute("read_file", {"path": "a.txt"})
        assert not result.is_error
        assert "内容" in result.content

    @pytest.mark.asyncio
    async def test_tier_clause_not_enforced_at_registry(self, tmp_path: Path):
        """tier 超限须走 PolicyHook 的越级奏请，registry 不越权硬拒。"""
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        registry = self._registry(tmp_path)

        with bind_persona(_persona(tool_tier_max=0)):
            result = await registry.execute("read_file", {"path": "a.txt"})
        assert not result.is_error  # T0 恒 ≤ tier_max，名单为空 → 放行

    @pytest.mark.asyncio
    async def test_no_persona_unchanged(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        registry = self._registry(tmp_path)
        result = await registry.execute("read_file", {"path": "a.txt"})
        assert not result.is_error


class TestMigrationV26:
    def test_placeholder_zero_bumped_intentional_kept(self, tmp_path: Path):
        import sqlite3

        from tianshu.storage.migration_ledger import apply_migrations
        from tianshu.storage.migrations import MIGRATIONS

        conn = sqlite3.connect(tmp_path / "t.db")
        conn.row_factory = sqlite3.Row
        # 建到 v25 为止（含 personas 表与 allowed_paths 列）
        upto = [m for m in MIGRATIONS if m.version <= 25]
        apply_migrations(conn, upto)
        conn.execute(
            "INSERT INTO personas (id, name, department, tools_allowed, tools_denied,"
            " skills_allowed, tool_tier_max, created_at, updated_at)"
            " VALUES ('legacy', '旧官', 'wenyuan', '[]', '[]', '[]', 0,"
            " datetime('now'), datetime('now'))"
        )
        conn.execute(
            "INSERT INTO personas (id, name, department, tools_allowed, tools_denied,"
            " skills_allowed, tool_tier_max, created_at, updated_at)"
            " VALUES ('curated', '有意声明', 'bingbu', '[]', '[]', '[]', 2,"
            " datetime('now'), datetime('now'))"
        )
        conn.commit()

        apply_migrations(conn, MIGRATIONS)

        rows = {
            r["id"]: r["tool_tier_max"]
            for r in conn.execute("SELECT id, tool_tier_max FROM personas")
        }
        assert rows["legacy"] == 4, "占位 0 须提到 4 保持既有行为"
        assert rows["curated"] == 2, "有意声明须原样保留"
