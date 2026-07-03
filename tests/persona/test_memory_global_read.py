"""persona memory_global_read 开关测试。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tianshu.memory.models import MemoryEntry
from tianshu.persona.model import AgentPersona
from tianshu.tools.memory_tools import _memory_search


def test_persona_field_round_trip(storage):
    storage.save_persona(
        {"id": "wym", "name": "王", "department": "neige", "memory_global_read": True}
    )
    assert storage.get_persona("wym")["memory_global_read"] is True
    storage.save_persona({"id": "x", "name": "X", "department": "neige"})
    assert storage.get_persona("x")["memory_global_read"] is False


def _persona(tmp_path, gr):
    return AgentPersona(
        id="wym",
        name="王",
        department="neige",
        soul_path=tmp_path / "s",
        role_path=tmp_path / "r",
        memory_path=tmp_path / "m",
        memory_global_read=gr,
    )


@pytest.mark.asyncio
async def test_global_read_off_hides_other_private(storage, tmp_path):
    storage.save_memory_entry(
        MemoryEntry(persona_id="other", category="observation", content="机密 secret-xyz")
    )
    with patch(
        "tianshu.kernel.ambient.get_current_persona", return_value=_persona(tmp_path, False)
    ):
        r = await _memory_search(storage, query="secret-xyz")
    assert "secret-xyz" not in r.content


@pytest.mark.asyncio
async def test_global_read_on_sees_all(storage, tmp_path):
    storage.save_memory_entry(
        MemoryEntry(persona_id="other", category="observation", content="机密 secret-xyz")
    )
    with patch("tianshu.kernel.ambient.get_current_persona", return_value=_persona(tmp_path, True)):
        r = await _memory_search(storage, query="secret-xyz")
    assert "secret-xyz" in r.content
