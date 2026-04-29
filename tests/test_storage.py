"""Tests for Storage CRUD operations."""

import pytest

from tianshu.models import (
    Decree,
    Edict,
    EdictRuntime,
    EdictSchedule,
    EdictStatus,
    Memorial,
    TaskStatus,
    UsageSummary,
)


class TestEdictStorage:
    def test_save_and_get(self, storage):
        edict = Edict(goal="test goal", title="test")
        storage.save_edict(edict)
        loaded = storage.get_edict(edict.id)
        assert loaded is not None
        assert loaded.goal == "test goal"
        assert loaded.title == "test"
        assert loaded.status == EdictStatus.OPEN

    def test_get_nonexistent(self, storage):
        assert storage.get_edict("nonexistent") is None

    def test_list_edicts(self, storage):
        for i in range(3):
            storage.save_edict(Edict(goal=f"goal {i}", title=f"t{i}"))
        edicts, total = storage.list_edicts()
        assert total == 3
        assert len(edicts) == 3

    def test_list_with_status_filter(self, storage):
        e1 = Edict(goal="a", status=EdictStatus.OPEN)
        e2 = Edict(goal="b", status=EdictStatus.COMPLETED)
        storage.save_edict(e1)
        storage.save_edict(e2)
        edicts, total = storage.list_edicts(status="open")
        assert total == 1
        assert edicts[0].goal == "a"

    def test_list_with_search(self, storage):
        storage.save_edict(Edict(goal="find the needle", title="needle"))
        storage.save_edict(Edict(goal="not this one", title="other"))
        edicts, total = storage.list_edicts(search="needle")
        assert total == 1

    def test_list_edicts_exclude_assistant_chat(self, storage):
        """v2: list_edicts(exclude_assistant_chat=True) 排除 chat 敕令。"""
        e1 = Edict(title="业务", goal="干活")
        e2 = Edict(title="聊天", goal="对话", metadata={"assistant_chat": True})
        storage.save_edict(e1)
        storage.save_edict(e2)

        all_edicts, all_total = storage.list_edicts()
        assert all_total == 2

        filtered, ftotal = storage.list_edicts(exclude_assistant_chat=True)
        assert ftotal == 1
        assert filtered[0].title == "业务"

    def test_update_edict(self, storage):
        edict = Edict(goal="original", title="orig")
        storage.save_edict(edict)
        storage.update_edict(edict.id, goal="updated")
        loaded = storage.get_edict(edict.id)
        assert loaded.goal == "updated"

    def test_delete_edict(self, storage):
        edict = Edict(goal="delete me")
        storage.save_edict(edict)
        storage.delete_edict(edict.id)
        assert storage.get_edict(edict.id) is None

    def test_update_status(self, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        storage.update_edict_status(edict.id, "completed")
        loaded = storage.get_edict(edict.id)
        assert loaded.status == EdictStatus.COMPLETED

    def test_phase1_fields_roundtrip(self, storage):
        edict = Edict(
            goal="complex",
            source="cli",
            priority="urgent",
            review_policy="always",
            constraints=["no shell"],
            schedule=EdictSchedule(type="once"),
            runtime=EdictRuntime(timeout_seconds=60, token_budget=5000),
            metadata={"key": "val"},
        )
        storage.save_edict(edict)
        loaded = storage.get_edict(edict.id)
        assert loaded.source == "cli"
        assert loaded.priority == "urgent"
        assert loaded.constraints == ["no shell"]
        assert loaded.runtime.timeout_seconds == 60
        assert loaded.runtime.token_budget == 5000
        assert loaded.metadata == {"key": "val"}


class TestMemorialStorage:
    def test_save_and_get(self, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id, instruction="do it")
        storage.save_memorial(memorial)
        loaded = storage.get_memorial(memorial.id)
        assert loaded is not None
        assert loaded.edict_id == edict.id
        assert loaded.instruction == "do it"

    def test_update_memorial(self, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id)
        storage.save_memorial(memorial)
        memorial.status = TaskStatus.COMPLETED
        memorial.result = "done"
        memorial.usage = UsageSummary(total_tokens=100)
        storage.update_memorial(memorial)
        loaded = storage.get_memorial(memorial.id)
        assert loaded.status == TaskStatus.COMPLETED
        assert loaded.result == "done"
        assert loaded.usage.total_tokens == 100

    def test_get_by_edict(self, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        m1 = Memorial(edict_id=edict.id, instruction="first")
        m2 = Memorial(edict_id=edict.id, instruction="second")
        storage.save_memorial(m1)
        storage.save_memorial(m2)
        latest = storage.get_memorial_by_edict(edict.id)
        assert latest is not None

    def test_list_by_edict(self, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        for i in range(3):
            storage.save_memorial(Memorial(edict_id=edict.id, instruction=f"m{i}"))
        memorials = storage.list_memorials_by_edict(edict.id)
        assert len(memorials) == 3

    def test_list_memorials(self, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        storage.save_memorial(Memorial(edict_id=edict.id, status=TaskStatus.COMPLETED))
        storage.save_memorial(Memorial(edict_id=edict.id, status=TaskStatus.FAILED))
        memorials, total = storage.list_memorials()
        assert total == 2
        filtered, ftotal = storage.list_memorials(status="completed")
        assert ftotal == 1

    def test_phase1_fields_roundtrip(self, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id,
            attempt=2,
            parent_memorial_id="prev-id",
            review_status="pending",
        )
        storage.save_memorial(memorial)
        loaded = storage.get_memorial(memorial.id)
        assert loaded.attempt == 2
        assert loaded.parent_memorial_id == "prev-id"
        assert loaded.review_status == "pending"


class TestDecreeStorage:
    def test_save_and_list(self, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id)
        storage.save_memorial(memorial)
        decree = Decree(memorial_id=memorial.id, action="approve", comment="LGTM")
        storage.save_decree(decree)
        decrees = storage.list_decrees_by_memorial(memorial.id)
        assert len(decrees) == 1
        assert decrees[0].action == "approve"
        assert decrees[0].comment == "LGTM"


class TestEventStorage:
    def test_append_and_get(self, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        storage.append_event(edict.id, None, "test.event", {"key": "val"})
        events = storage.get_events(edict.id)
        assert len(events) == 1
        assert events[0]["event_type"] == "test.event"
        assert events[0]["payload"]["key"] == "val"
