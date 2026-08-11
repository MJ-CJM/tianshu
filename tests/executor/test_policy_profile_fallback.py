"""敕令未显式配 policy profile 时，回落到承办官员的 allowed_paths（issue #35）。

背景：权限是官员的固有属性而非渠道的。从飞书/Telegram 进来的敕令拿不到显式
profile（EdictBridge / submit_edict 都不设），若无此回落，`allowed_paths` 这条
事前授权通道对 IM 场景等于不存在——#34 修好的绝对 glob 授权也就用不上。

关键链路：回落结果必须挂到 `edict.runtime.policy_profile` 上，因为
WorkspaceBoundaryRule 判定越界时读的是它（`_resolve_profile_globs`），
而不是 expand_profile_to_rules 展开出来的 session rules。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tianshu.executor.executor import Executor
from tianshu.models.edict import Edict, PolicyProfilePayload
from tianshu.models.memorial import Memorial
from tianshu.tools.policy_rules.workspace_boundary import WorkspaceBoundaryRule
from tianshu.tools.types import ToolTier

WORKSPACE = Path("/tmp/tianshu-ws").resolve()
OUTSIDE = "/data/shared/report.csv"


class _RecordingRuleStore:
    """SessionRuleStore 协议里 expand_profile_to_rules 用到的那一个方法。"""

    def __init__(self) -> None:
        self.rules: list[object] = []

    async def create(self, rule: object) -> None:
        self.rules.append(rule)


class _RecordingStorage:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def append_event(self, _edict_id, _memorial_id, event_type, payload) -> None:  # type: ignore[no-untyped-def]
        self.events.append((event_type, payload))


def _executor(persona_allowed: tuple[str, ...] | None) -> SimpleNamespace:
    """最小依赖桩：只装 _expand_policy_profile 用得到的三个协作者。"""
    persona = (
        SimpleNamespace(id="smg", allowed_paths=list(persona_allowed))
        if persona_allowed is not None
        else None
    )
    stub = SimpleNamespace(
        _persona_loader=SimpleNamespace(get=lambda _pid: persona),
        _session_rule_store=_RecordingRuleStore(),
        _storage=_RecordingStorage(),
    )
    # _expand_policy_profile 内部会调 self._persona_fallback_profile，绑上真实实现
    stub._persona_fallback_profile = lambda m: Executor._persona_fallback_profile(stub, m)
    return stub


def _edict(profile: PolicyProfilePayload | None = None) -> Edict:
    edict = Edict(goal="读一份工作区外的报表")
    edict.runtime.policy_profile = profile
    return edict


def _memorial(edict: Edict, persona_id: str | None = "smg") -> Memorial:
    return Memorial(edict_id=edict.id, instruction="x", persona_id=persona_id)


async def _expand(stub: SimpleNamespace, edict: Edict, memorial: Memorial) -> None:
    await Executor._expand_policy_profile(stub, edict, memorial)  # type: ignore[arg-type]


async def _boundary_verdict(edict: Edict, path: str) -> str | None:
    """把 edict 交给 WorkspaceBoundaryRule 判定，返回 verdict（None=弃权即放行）。"""
    ctx = SimpleNamespace(
        tool_name="read_file",
        args={"path": path},
        tool_tier=ToolTier.T0_READONLY,
        edict=edict,
        memorial=None,
        workspace_root=WORKSPACE,
        iteration=0,
        recent_calls=(),
    )
    decision = await WorkspaceBoundaryRule().evaluate(ctx)  # type: ignore[arg-type]
    return decision.verdict if decision else None


class TestFallbackAppliesPersonaAllowlist:
    @pytest.mark.asyncio
    async def test_persona_allowlist_lands_on_edict_runtime(self):
        stub = _executor(("/data/shared/**",))
        edict = _edict()
        await _expand(stub, edict, _memorial(edict))

        assert edict.runtime.policy_profile is not None
        assert edict.runtime.policy_profile.allowed_paths == ["/data/shared/**"]
        assert edict.runtime.policy_profile.template_name == "persona:smg"

    @pytest.mark.asyncio
    async def test_boundary_rule_honours_the_fallback(self):
        """整条链：官员白名单 → edict.runtime → WorkspaceBoundaryRule 放行。"""
        edict = _edict()
        assert await _boundary_verdict(edict, OUTSIDE) == "deny"  # 回落前：越界

        await _expand(_executor(("/data/shared/**",)), edict, _memorial(edict))
        assert await _boundary_verdict(edict, OUTSIDE) is None  # 回落后：弃权=放行

    @pytest.mark.asyncio
    async def test_event_records_source_persona(self):
        stub = _executor(("/data/shared/**",))
        edict = _edict()
        await _expand(stub, edict, _memorial(edict))

        kinds = [
            payload for name, payload in stub._storage.events if name == "policy.profile_applied"
        ]
        assert kinds and kinds[0]["source"] == "persona"


class TestExplicitProfileWins:
    @pytest.mark.asyncio
    async def test_explicit_profile_not_overwritten_by_persona(self):
        explicit = PolicyProfilePayload(allowed_paths=["/explicit/**"], template_name="by-hand")
        edict = _edict(explicit)
        await _expand(_executor(("/data/shared/**",)), edict, _memorial(edict))

        assert edict.runtime.policy_profile is not None
        assert edict.runtime.policy_profile.allowed_paths == ["/explicit/**"]

    @pytest.mark.asyncio
    async def test_event_records_source_edict(self):
        stub = _executor(("/data/shared/**",))
        edict = _edict(PolicyProfilePayload(allowed_paths=["/explicit/**"]))
        await _expand(stub, edict, _memorial(edict))

        payloads = [p for name, p in stub._storage.events if name == "policy.profile_applied"]
        assert payloads and payloads[0]["source"] == "edict"


class TestNoFallbackKeepsCurrentBehaviour:
    """安全底线：不配置时行为与回落前逐字节一致，本机制不主动放权。"""

    @pytest.mark.asyncio
    async def test_persona_without_allowlist_leaves_profile_none(self):
        edict = _edict()
        await _expand(_executor(()), edict, _memorial(edict))
        assert edict.runtime.policy_profile is None
        assert await _boundary_verdict(edict, OUTSIDE) == "deny"

    @pytest.mark.asyncio
    async def test_missing_persona_leaves_profile_none(self):
        edict = _edict()
        await _expand(_executor(None), edict, _memorial(edict))
        assert edict.runtime.policy_profile is None

    @pytest.mark.asyncio
    async def test_memorial_without_persona_id_leaves_profile_none(self):
        edict = _edict()
        await _expand(_executor(("/data/shared/**",)), edict, _memorial(edict, persona_id=None))
        assert edict.runtime.policy_profile is None

    @pytest.mark.asyncio
    async def test_relative_glob_from_persona_still_cannot_escape(self):
        """#34 的语义不被绕过：相对 glob 授权不了界外路径，无论来源。"""
        edict = _edict()
        await _expand(_executor(("**/*",)), edict, _memorial(edict))
        assert await _boundary_verdict(edict, "/etc/passwd") == "deny"
