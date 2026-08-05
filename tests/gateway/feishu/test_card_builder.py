"""CardBuilder 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tianshu.gateway.feishu.card_builder import CardBuilder


class _E:
    def __init__(self, id: str, title: str, status: str) -> None:
        self.id, self.title, self.status = id, title, status


def _button_commands(card: dict) -> list[str]:
    """卡片 action 区所有按钮的 command。"""
    return [
        btn["value"]["command"]
        for el in card["elements"]
        if el.get("tag") == "action"
        for btn in el["actions"]
    ]


def test_menu_card():
    """助手模式菜单：命令清单 + 快捷按钮。"""
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    card = cb.build_menu_card()
    assert card["header"]["template"] == "purple"
    md = card["elements"][0]["content"]
    for cmd in ("/list", "/new", "/select", "/budget", "/clear", "/help"):
        assert cmd in md
    assert set(_button_commands(card)) == {"list", "budget", "clear"}


def test_menu_card_in_edict_mode_offers_exit():
    """敕令模式菜单必须给出 /exit 出口——助手命令表里没有它，用户会困在敕令里。

    回归（2026-08-04）：敕令模式下 /menu 委托给同一实现，此前一律渲染助手命令表。
    """
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    card = cb.build_menu_card(edict_id="01KZ5XFDFF5MVPBR")
    assert "01KZ5XFD" in card["header"]["title"]["content"]
    md = card["elements"][0]["content"]
    assert "/exit" in md
    assert "/status" in md
    commands = _button_commands(card)
    assert "exit" in commands  # 「退出敕令」按钮
    assert "clear" not in commands  # /clear 只在助手模式有意义


def _rows(card: dict) -> list[dict]:
    """卡片里的敕令行（div 元素）。"""
    return [el for el in card["elements"] if el.get("tag") == "div"]


def test_list_card_marks_anchor_with_star():
    """当前 anchor 行加 ★ 标记，且只有它带 ★。"""
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    edicts = [_E("ed_a", "写代码", "open"), _E("ed_b", "总结", "open")]
    card = cb.build_list_card(edicts, current_anchor="ed_a")
    starred = [r for r in _rows(card) if "★" in r["text"]["content"]]
    assert len(starred) == 1
    assert "ed_a"[:8] in starred[0]["text"]["content"]


def test_list_card_switch_button_carries_full_id():
    """非 anchor 行带「切换」按钮，value 走 CardActionDispatcher 的 select 协议。

    edict_id 必须是完整 ID：合成的 `/select <id>` 要过 _cmd_select 的 ≥6 字符校验，
    且完整 ID 才能保证唯一匹配。
    """
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    edicts = [_E("ed_anchor_1234", "写代码", "open"), _E("ed_other_5678", "总结", "open")]
    card = cb.build_list_card(edicts, current_anchor="ed_anchor_1234")
    buttons = [r["extra"] for r in _rows(card) if "extra" in r]
    # anchor 那行不给按钮（点了是空操作）
    assert len(buttons) == 1
    assert buttons[0]["value"] == {"command": "select", "edict_id": "ed_other_5678"}


def test_list_card_truncates_long_title():
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    edicts = [_E("ed_a", "x" * 100, "open")]
    card = cb.build_list_card(edicts)
    assert "x" * 31 not in _rows(card)[0]["text"]["content"]


def test_list_card_has_footer_actions_and_text_fallback():
    """底部快捷按钮 + 文本命令退路（按钮失灵时仍可操作）。"""
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    card = cb.build_list_card([_E("ed_a", "x", "open")])
    actions = [el for el in card["elements"] if el.get("tag") == "action"]
    assert len(actions) == 1
    commands = {btn["value"]["command"] for btn in actions[0]["actions"]}
    assert commands == {"list", "budget"}
    notes = [el for el in card["elements"] if el.get("tag") == "note"]
    assert "/select" in notes[0]["elements"][0]["content"]


@pytest.mark.asyncio
async def test_budget_card_no_cost_manager():
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    card = await cb.build_budget_card()
    assert card["header"]["template"] == "grey"
    assert "暂时无法获取" in card["elements"][0]["content"]


@pytest.mark.asyncio
async def test_budget_card_with_data():
    storage = MagicMock()
    fetchall_cursor = MagicMock()
    fetchall_cursor.fetchall.return_value = [("ed_a", 3.21), ("ed_b", 2.10)]
    fetchone_cursor = MagicMock()
    fetchone_cursor.fetchone.return_value = (5.31,)
    # _conn.execute 第 1 次返回 group-by 行，第 2 次返回总和
    storage._conn.execute.side_effect = [fetchall_cursor, fetchone_cursor]
    storage.get_edict.return_value = MagicMock(title="测试敕令")
    cm = MagicMock()
    budget = MagicMock()
    budget.budget_cny = 100.0
    budget.spent_cny = 5.31
    cm.get_budget.return_value = budget
    cb = CardBuilder(storage=storage, cost_manager=cm)
    card = await cb.build_budget_card()
    assert card["header"]["template"] == "orange"
    md_content = card["elements"][0]["content"]
    assert "近 7 天消费" in md_content
    assert "¥5.31" in md_content


@pytest.mark.asyncio
async def test_budget_card_query_failure_falls_back_gracefully():
    """cost_ledger 查询失败仍返回 orange 卡片（lines 内有预算条目）。"""
    storage = MagicMock()
    storage._conn.execute.side_effect = RuntimeError("db gone")
    cm = MagicMock()
    budget = MagicMock()
    budget.budget_cny = 100.0
    budget.spent_cny = 0.0
    cm.get_budget.return_value = budget
    cb = CardBuilder(storage=storage, cost_manager=cm)
    card = await cb.build_budget_card()
    # ledger 失败时 recent_total 仍是 0.0，但卡片仍是 orange 模板
    assert card["header"]["template"] == "orange"
