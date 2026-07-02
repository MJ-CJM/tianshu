"""PersonaRenderer 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from tianshu.gateway.feishu.persona_renderer import (
    DEFAULT_EMOJI,
    DEFAULT_NAME,
    PersonaRenderer,
)


def test_renderer_with_persona():
    p = MagicMock()
    p.name = "通政司"
    p.department = "tongzheng"
    r = PersonaRenderer(p)
    assert r.name == "通政司"
    assert r.emoji == "📜"
    assert "通政司" in r.welcome()
    assert "💼" in r.assistant_tag()


def test_renderer_with_unknown_department():
    p = MagicMock()
    p.name = "测试"
    p.department = "unknown_dept"
    r = PersonaRenderer(p)
    assert r.emoji == DEFAULT_EMOJI


def test_renderer_with_none_persona():
    r = PersonaRenderer(None)
    assert r.name == DEFAULT_NAME
    assert r.emoji == DEFAULT_EMOJI


def test_edict_tag_truncates_id():
    r = PersonaRenderer(None)
    assert r.edict_tag("ed_abcdef1234567890") == "📋 敕令 #ed_abcde"


def test_help_modes_differ():
    r = PersonaRenderer(None)
    a = r.help_assistant()
    e = r.help_edict("ed_xxx")
    assert "/new" in a and "/list" in a
    assert "/exit" in e and "续接" in e


def test_unknown_command_reply():
    r = PersonaRenderer(None)
    msg = r.unknown_command_reply("💼 助手", "/foo")
    assert "/foo" in msg and "/help" in msg


def test_edict_reply_methods():
    r = PersonaRenderer(None)
    assert "ed_abcde" in r.edict_received_reply("ed_abcdef")
    assert "ed_abcde" in r.edict_selected_reply("ed_abcdef", "title")
    assert "ed_abcde" in r.edict_cancel_reply("ed_abcdef")
    assert "助手" in r.edict_exit_reply()
    assert "list" in r.llm_intent_hint("/list")
