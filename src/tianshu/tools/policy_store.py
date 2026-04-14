"""SessionRuleStore — 信任缓存层（edict / always scope）。

Spec Section 4。两种实现：
- InMemorySessionRuleStore：edict scope（进程内）
- SqliteSessionRuleStore：always scope（持久化）— Task 3.2 实现
组合使用：CompositeSessionRuleStore 先查 in-memory，再查 sqlite。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionRule:
    rule_id: str
    tool_name: str
    arg_fingerprint: str
    scope: Literal["edict", "always"]
    edict_id: str | None
    granted_at: datetime
    granted_by_decree_id: str | None
    source: Literal["approval", "profile", "manual"]
    reason: str
    expires_at: datetime | None


# ---------- arg_fingerprint 函数族 ----------


def fingerprint_edit_file(args: dict) -> str:
    """edit_file 指纹 = dirname(path)。覆盖同目录下所有编辑。"""
    import os
    path = args.get("path") or args.get("file_path") or ""
    return f"dir:{os.path.dirname(path) or '.'}"


def fingerprint_bash(args: dict) -> str:
    """bash 指纹 = 命令前两个 token。例如 'git push origin main' → 'git push'。"""
    cmd = (args.get("command") or "").strip()
    tokens = cmd.split()[:2]
    return "bash:" + " ".join(tokens)


def fingerprint_memory(args: dict) -> str:
    """memory_tools 指纹 = sorted filtered arg keys。"""
    keys = sorted(k for k in args.keys() if k not in {"value", "content"})
    return "memory:" + ",".join(keys)


def fingerprint_default(args: dict) -> str:
    """默认：args 稳定 JSON → sha1，用于严格相等匹配。"""
    try:
        canonical = json.dumps(args, sort_keys=True, default=str)
    except Exception:
        canonical = repr(sorted(args.items()))
    return "hash:" + hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


_FINGERPRINT_FUNCS: dict[str, "callable"] = {
    "edit_file": fingerprint_edit_file,
    "write_file": fingerprint_edit_file,
    "shell_exec": fingerprint_bash,
    "bash": fingerprint_bash,
}


def compute_fingerprint(tool_name: str, args: dict) -> str:
    """根据 tool_name 选用对应算法。memory_tools.* 前缀走 memory 算法。"""
    if tool_name in _FINGERPRINT_FUNCS:
        return _FINGERPRINT_FUNCS[tool_name](args)
    if tool_name.startswith("memory_"):
        return fingerprint_memory(args)
    return fingerprint_default(args)


# ---------- Protocol ----------


class SessionRuleStore(Protocol):
    async def create(self, rule: SessionRule) -> None: ...

    async def find_match(
        self, tool_name: str, args: dict, edict_id: str | None,
    ) -> SessionRule | None: ...

    async def list_by_scope(
        self, scope: str, edict_id: str | None = None,
    ) -> list[SessionRule]: ...

    async def revoke(self, rule_id: str) -> None: ...

    async def clear_edict(self, edict_id: str) -> None: ...


# ---------- In-memory 实现 ----------


@dataclass
class InMemorySessionRuleStore:
    """edict scope 的规则 — 进程内 dict。"""

    _rules: dict[str, SessionRule] = field(default_factory=dict)

    async def create(self, rule: SessionRule) -> None:
        self._rules[rule.rule_id] = rule

    async def find_match(
        self, tool_name: str, args: dict, edict_id: str | None,
    ) -> SessionRule | None:
        fp = compute_fingerprint(tool_name, args)
        now = datetime.now(UTC)
        for rule in self._rules.values():
            if rule.tool_name != tool_name:
                continue
            if rule.arg_fingerprint != fp:
                continue
            if rule.scope == "edict" and rule.edict_id != edict_id:
                continue
            if rule.expires_at and rule.expires_at < now:
                continue
            return rule
        return None

    async def list_by_scope(
        self, scope: str, edict_id: str | None = None,
    ) -> list[SessionRule]:
        out = []
        for rule in self._rules.values():
            if rule.scope != scope:
                continue
            if edict_id is not None and rule.edict_id != edict_id:
                continue
            out.append(rule)
        return out

    async def revoke(self, rule_id: str) -> None:
        self._rules.pop(rule_id, None)

    async def clear_edict(self, edict_id: str) -> None:
        self._rules = {
            rid: r for rid, r in self._rules.items() if r.edict_id != edict_id
        }


# ---------- Composite（InMemory + Sqlite） ----------


@dataclass
class CompositeSessionRuleStore:
    """组合存储：edict scope 走 InMemory，always scope 走 Sqlite。"""

    in_memory: InMemorySessionRuleStore
    sqlite: "SqliteSessionRuleStore"  # noqa: F821

    async def create(self, rule: SessionRule) -> None:
        if rule.scope == "always":
            await self.sqlite.create(rule)
        else:
            await self.in_memory.create(rule)

    async def find_match(
        self, tool_name: str, args: dict, edict_id: str | None,
    ) -> SessionRule | None:
        hit = await self.in_memory.find_match(tool_name, args, edict_id)
        if hit is not None:
            return hit
        return await self.sqlite.find_match(tool_name, args, edict_id)

    async def list_by_scope(
        self, scope: str, edict_id: str | None = None,
    ) -> list[SessionRule]:
        if scope == "always":
            return await self.sqlite.list_by_scope(scope, edict_id)
        return await self.in_memory.list_by_scope(scope, edict_id)

    async def revoke(self, rule_id: str) -> None:
        await self.in_memory.revoke(rule_id)
        await self.sqlite.revoke(rule_id)

    async def clear_edict(self, edict_id: str) -> None:
        await self.in_memory.clear_edict(edict_id)


# ---------- Factory helpers ----------


def make_session_rule(
    *,
    tool_name: str,
    arg_fingerprint: str,
    scope: Literal["edict", "always"],
    source: Literal["approval", "profile", "manual"],
    reason: str,
    edict_id: str | None = None,
    granted_by_decree_id: str | None = None,
    expires_after: timedelta | None = None,
) -> SessionRule:
    now = datetime.now(UTC)
    expires_at: datetime | None = None
    if expires_after is not None:
        expires_at = now + expires_after
    elif scope == "always":
        expires_at = now + timedelta(days=30)  # 默认 30 天
    return SessionRule(
        rule_id=str(uuid.uuid4()),
        tool_name=tool_name,
        arg_fingerprint=arg_fingerprint,
        scope=scope,
        edict_id=edict_id,
        granted_at=now,
        granted_by_decree_id=granted_by_decree_id,
        source=source,
        reason=reason,
        expires_at=expires_at,
    )


def assert_can_grant(tool_name: str, scope: str) -> None:
    """BashSafetyRule 的硬约束：bash 不能 always scope。

    调用方在 create 前调用；违规抛 ValueError 上层捕获并降级。
    """
    if scope == "always" and tool_name in {"shell_exec", "bash"}:
        raise ValueError(
            f"Cannot grant 'always' scope to bash-family tool {tool_name!r}"
        )
