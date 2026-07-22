"""ambient Edict / Persona ContextVar：让 tool handler 拿到当前 Edict 与 Persona。

Persona ambient 是 V1 自学习记忆系统（memory_write 工具）能正确解析
caller_persona_id 的关键——agent.py 在 LLM 循环里同时 bind 当前 edict 与 persona，
工具内通过 get_current_persona() 拿到调用方身份，避免 agent 显式传 persona_id 走串。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

from tianshu.models import Edict

if TYPE_CHECKING:
    from tianshu.persona.model import AgentPersona

_current_edict: ContextVar[Edict | None] = ContextVar("current_edict", default=None)
_current_persona: ContextVar[AgentPersona | None] = ContextVar(
    "current_persona",
    default=None,
)
_current_tool_invocation_id: ContextVar[str | None] = ContextVar(
    "current_tool_invocation_id",
    default=None,
)


def get_current_edict() -> Edict | None:
    return _current_edict.get()


def get_current_persona() -> AgentPersona | None:
    return _current_persona.get()


def get_current_tool_invocation_id() -> str | None:
    return _current_tool_invocation_id.get()


@contextmanager
def bind_edict(edict: Edict):
    token: Token = _current_edict.set(edict)
    try:
        yield
    finally:
        _current_edict.reset(token)


@contextmanager
def bind_persona(persona: AgentPersona | None):
    token: Token = _current_persona.set(persona)
    try:
        yield
    finally:
        _current_persona.reset(token)


@contextmanager
def bind_tool_invocation_id(invocation_id: str | None):
    token: Token = _current_tool_invocation_id.set(invocation_id)
    try:
        yield
    finally:
        _current_tool_invocation_id.reset(token)
