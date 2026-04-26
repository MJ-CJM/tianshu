"""checkpoint + resume 测试。"""

from __future__ import annotations

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


@pytest.mark.integration
async def test_resume_from_checkpoint(tmp_path):
    storage = Storage(str(tmp_path / "t.db"))
    if hasattr(storage, "init_db"):
        storage.init_db()
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
        edict_id=e.id, iteration=2, current_level="L0",
        same_issue_streak=1, last_critic_issue_class="factual_error",
        total_cost_cny=0.2,
    )
    actor = MagicMock()
    actor.execute = AsyncMock(return_value=MagicMock(
        result="recovered", summary="recovered", usage=MagicMock(cost_cny=0.1),
    ))
    actor_llm = MagicMock()
    critic_llm = MagicMock()
    critic_llm.chat = AsyncMock(return_value=MagicMock(
        content='{"verdict": "pass", "feedback": "good"}',
    ))
    ctx = OrchestratorContext(
        agent=actor, storage=storage, bus=bus,
        actor_llm=actor_llm, critic_llm=critic_llm,
    )
    _save_checkpoint(ctx, pre_state)

    r = await run(e, Memorial(edict_id=e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    # 从 iteration=2 续跑，下一轮是 iteration 2（actor 调一次，critic 通过），最终 state.iteration == 3
    assert r.state.iteration == 3
    # checkpoint 已被清
    assert storage.get_outer_loop_checkpoint(e.id) is None
