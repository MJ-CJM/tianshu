"""CardBuilder 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tianshu.gateway.feishu.card_builder import CardBuilder


class _E:
    def __init__(self, id: str, title: str, status: str) -> None:
        self.id, self.title, self.status = id, title, status


def test_menu_card():
    """v2: /menu 卡片不再含 callback button（飞书 ws 不支持卡片回调）；改为命令列表 markdown。"""
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    card = cb.build_menu_card()
    assert card["header"]["template"] == "purple"
    # 不应有 action 元素（callback button）
    has_action = any(el.get("tag") == "action" for el in card["elements"])
    assert not has_action
    # markdown 内含命令清单
    md = card["elements"][0]["content"]
    for cmd in ("/list", "/new", "/select", "/budget", "/clear", "/help"):
        assert cmd in md


def test_list_card_marks_anchor_with_star():
    """v2: /list 不再含 button，所有敕令在单 markdown 块内。当前 anchor 加 ★ 标记。"""
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    edicts = [_E("ed_a", "写代码", "open"), _E("ed_b", "总结", "open")]
    card = cb.build_list_card(edicts, current_anchor="ed_a")
    has_action = any(el.get("tag") == "action" for el in card["elements"])
    assert not has_action
    md = card["elements"][0]["content"]
    assert "★" in md
    assert "/select" in md
    # ed_b 不应有 ★ —— 验证仅 anchor 行带 ★
    lines = md.split("\n")
    star_lines = [line for line in lines if "★" in line]
    assert len(star_lines) == 1


def test_list_card_truncates_long_title():
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    long_title = "x" * 100
    edicts = [_E("ed_a", long_title, "open")]
    card = cb.build_list_card(edicts)
    # Title 截到 30 字符内
    md = card["elements"][0]["content"]
    # 单个敕令的 title 部分应 ≤ 30 字符
    assert "x" * 31 not in md


def test_list_card_contains_select_hint():
    """v2: /list 提示用户用 /select <ID> 切换。"""
    cb = CardBuilder(storage=MagicMock(), cost_manager=None)
    edicts = [_E("ed_a", "x", "open")]
    card = cb.build_list_card(edicts)
    md = card["elements"][0]["content"]
    assert "/select" in md
    assert "复制" in md or "短 ID" in md


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
