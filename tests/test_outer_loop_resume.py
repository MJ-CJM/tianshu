"""checkpoint + resume 测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.executor.orchestrator import OrchestratorContext, run
from tianshu.executor.orchestrator.loop import _save_checkpoint
from tianshu.executor.orchestrator.state import OuterLoopState
from tianshu.models.acceptance import AcceptanceCriteria, CriticSpec
from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict
from tianshu.models.memorial import Memorial
from tianshu.storage import Storage

_AUDIT_PASS_JSON = json.dumps({"passed": True, "gaps": []})


@pytest.mark.integration
async def test_resume_from_checkpoint(tmp_path):
    storage = Storage(str(tmp_path / "t.db"))
    if hasattr(storage, "init_db"):
        storage.init_db()
    try:
        bus = MagicMock(spec=EventBus)
        bus.emit = AsyncMock()

        e = Edict(
            goal="g",
            acceptance=AcceptanceCriteria(
                max_outer_iterations=5,
                critic=CriticSpec(same_issue_threshold=2),
            ),
            execution_profile="checkpointed",
        )
        storage.save_edict(e)

        # 模拟"上次跑了 2 轮被打断" —— 手动写一个 state 到 checkpoint
        pre_state = OuterLoopState(
            edict_id=e.id,
            iteration=2,
            current_level="L0",
            same_issue_streak=1,
            last_critic_issue_class="factual_error",
            total_cost_cny=0.2,
        )
        actor = MagicMock()
        actor.execute = AsyncMock(
            return_value=MagicMock(
                result="recovered",
                summary="recovered",
                usage=MagicMock(cost_cny=0.1),
            )
        )
        actor_llm = MagicMock()
        critic_llm = MagicMock()
        # Task 9 加了 completion audit 门，critic pass 后会再调一次 critic_llm 跑 audit；
        # 需要在 critic-pass 响应后追加 audit-pass JSON，否则 audit 解析失败导致无限续转。
        critic_llm.chat = AsyncMock(
            side_effect=[
                MagicMock(content='{"verdict": "pass", "feedback": "good"}'),
                MagicMock(content=_AUDIT_PASS_JSON),
            ]
        )
        ctx = OrchestratorContext(
            agent=actor,
            storage=storage,
            bus=bus,
            actor_llm=actor_llm,
            critic_llm=critic_llm,
        )
        _save_checkpoint(ctx, pre_state)

        r = await run(e, Memorial(edict_id=e.id), ctx)
        assert r.status == TaskStatus.COMPLETED
        # 从 iteration=2 续跑，下一轮是 iteration 2（actor 调一次，critic 通过），最终 state.iteration == 3
        assert r.state.iteration == 3
        # run() 只产出终态结果；Memorial 还未由上层落库前必须保留 checkpoint。
        assert storage.get_outer_loop_checkpoint(e.id) is not None
    finally:
        storage.close()
