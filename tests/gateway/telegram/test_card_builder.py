"""TelegramCardBuilder 测试 —— build_budget_card 真实 cost_ledger SQL 路径。

feishu 侧同名场景（tests/gateway/feishu/test_card_builder.py）用 mock 过 storage._conn，
没有真正跑过 query_budget_data 的 SQL；这里改用真 Storage(:memory:) + CostManager
落一条真实 cost_ledger 记录，覆盖实际 SQL 查询路径。
"""

from __future__ import annotations

import pytest

from tianshu.cost.manager import CostManager
from tianshu.cost.models import CostRecord
from tianshu.gateway.telegram.card_builder import TelegramCardBuilder
from tianshu.models.edict import Edict
from tianshu.storage import Storage


@pytest.mark.asyncio
async def test_build_budget_card_real_sql():
    db = Storage(":memory:")
    db.init_db()
    try:
        edict = Edict(title="调研任务", goal="调研预算")
        db.save_edict(edict)

        cm = CostManager(db)
        cm.set_budget("global", 100.0)
        cm.record(
            CostRecord(
                edict_id=edict.id,
                cost_cny=12.5,
                model="gpt",
                provider_name="x",
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
            )
        )

        builder = TelegramCardBuilder(storage=db, cost_manager=cm)
        text, keyboard = await builder.build_budget_card()
    finally:
        db.close()

    assert keyboard is None
    assert "近 7 天消费" in text
    assert "¥12.50" in text
    assert "¥100.00" in text
    assert "调研任务" in text
