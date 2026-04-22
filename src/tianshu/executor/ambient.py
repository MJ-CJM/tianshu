"""ambient Edict ContextVar：让 tool handler 拿到当前 Edict，不用显式传参。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token

from tianshu.models import Edict

_current_edict: ContextVar[Edict | None] = ContextVar("current_edict", default=None)


def get_current_edict() -> Edict | None:
    return _current_edict.get()


@contextmanager
def bind_edict(edict: Edict):
    token: Token = _current_edict.set(edict)
    try:
        yield
    finally:
        _current_edict.reset(token)
