"""steer 中途注入(迭代 5「执行 2.0」)——长任务在线纠偏,下一轮 actor 吸收。"""

from __future__ import annotations

from types import SimpleNamespace

from tianshu.executor.orchestrator.loop import _inject_steer, _save_checkpoint
from tianshu.executor.orchestrator.state import OuterLoopState


class TestSteerStorage:
    def test_save_list_clear_roundtrip(self, storage):
        storage.save_steer("s1", "e1", "考虑并发", "2026-01-01T00:00:00+00:00")
        storage.save_steer("s2", "e1", "用 async", "2026-01-01T00:01:00+00:00")
        notes = storage.list_and_clear_steers("e1")
        assert notes == ["考虑并发", "用 async"]
        # 取即消费,不重复
        assert storage.list_and_clear_steers("e1") == []

    def test_scoped_by_edict(self, storage):
        storage.save_steer("s1", "e1", "a", "2026-01-01T00:00:00+00:00")
        storage.save_steer("s2", "e2", "b", "2026-01-01T00:00:00+00:00")
        assert storage.list_and_clear_steers("e1") == ["a"]
        assert storage.list_and_clear_steers("e2") == ["b"]


class TestInjectSteer:
    def test_inject_sets_steer_note(self, storage):
        storage.save_steer("s1", "e1", "记得测试", "2026-01-01T00:00:00+00:00")
        ctx = SimpleNamespace(storage=storage)
        edict = SimpleNamespace(id="e1")
        state = OuterLoopState(edict_id="e1")
        new_state = _inject_steer(ctx, edict, state)
        assert new_state.steer_note == "记得测试"
        assert new_state.steer_ids == ("s1",)
        assert storage.list_steers("e1") == [{"id": "s1", "note": "记得测试"}]

        _save_checkpoint(ctx, new_state)
        assert storage.list_steers("e1") == []

    def test_inject_clears_when_no_pending(self, storage):
        ctx = SimpleNamespace(storage=storage)
        edict = SimpleNamespace(id="e1")
        # 上一轮有 steer_note,本轮无 pending → 清空(不重复注入)
        state = OuterLoopState(edict_id="e1", steer_note="旧的")
        new_state = _inject_steer(ctx, edict, state)
        assert new_state.steer_note is None
        assert new_state.steer_ids == ()

    def test_multiple_steers_joined(self, storage):
        storage.save_steer("s1", "e1", "第一条", "2026-01-01T00:00:00+00:00")
        storage.save_steer("s2", "e1", "第二条", "2026-01-01T00:01:00+00:00")
        ctx = SimpleNamespace(storage=storage)
        new_state = _inject_steer(ctx, SimpleNamespace(id="e1"), OuterLoopState(edict_id="e1"))
        assert new_state.steer_note == "第一条\n第二条"

    def test_crash_before_checkpoint_keeps_steer_pending(self, storage):
        storage.save_steer("s1", "e1", "不能丢", "2026-01-01T00:00:00+00:00")
        ctx = SimpleNamespace(storage=storage)

        first_attempt = _inject_steer(
            ctx,
            SimpleNamespace(id="e1"),
            OuterLoopState(edict_id="e1"),
        )
        restarted_attempt = _inject_steer(
            ctx,
            SimpleNamespace(id="e1"),
            OuterLoopState(edict_id="e1"),
        )

        assert first_attempt.steer_note == restarted_attempt.steer_note == "不能丢"
        assert storage.list_steers("e1") == [{"id": "s1", "note": "不能丢"}]


class TestActorOverrideInjection:
    def test_steer_note_surfaces_in_actor_extra_msg(self):
        """steer_note 经 derive_actor_override 注入 actor 的 extra_system_msg。"""
        from tianshu.executor.orchestrator.loop import derive_actor_override
        from tianshu.models.acceptance import AcceptanceCriteria

        edict = SimpleNamespace(acceptance=AcceptanceCriteria())
        state = OuterLoopState(edict_id="e1", steer_note="优先用现有工具")
        override = derive_actor_override(state, edict)
        assert override.extra_system_msg is not None
        assert "优先用现有工具" in override.extra_system_msg
        assert "steer" in override.extra_system_msg
