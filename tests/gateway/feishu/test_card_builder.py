"""CardBuilder 单元测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tianshu.gateway.feishu.card_builder import CardBuilder


class _E:
    def __init__(self, id: str, title: str, status: str) -> None:
        self.id, self.title, self.status = id, title, status


def test_menu_card():
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    card = cb.build_menu_card()
    assert card["header"]["template"] == "purple"
    actions = card["elements"][1]["actions"]
    assert {a["value"]["command"] for a in actions} == {"list", "budget", "help"}


def test_list_card_marks_anchor_with_star():
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    edicts = [_E("ed_a", "写代码", "open"), _E("ed_b", "总结", "open")]
    card = cb.build_list_card(edicts, current_anchor="ed_a")
    assert "★" in card["elements"][0]["content"]
    assert card["elements"][1]["actions"][0]["type"] == "primary"
    assert "★" not in card["elements"][3]["content"]


def test_list_card_truncates_long_title():
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    long_title = "x" * 100
    edicts = [_E("ed_a", long_title, "open")]
    card = cb.build_list_card(edicts)
    # Title 截到 30 字符内，加 prefix 也远小于 100
    assert len(card["elements"][0]["content"]) < 100


def test_list_card_no_hr_for_single_edict():
    """单条敕令不应有 hr 分割线。"""
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    edicts = [_E("ed_a", "x", "open")]
    card = cb.build_list_card(edicts)
    tags = [el["tag"] for el in card["elements"]]
    assert "hr" not in tags


def test_list_card_default_button_when_not_anchor():
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    edicts = [_E("ed_a", "x", "open")]
    card = cb.build_list_card(edicts, current_anchor="ed_other")
    assert card["elements"][1]["actions"][0]["type"] == "default"


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
