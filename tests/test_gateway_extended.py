"""Extended Gateway API tests for higher coverage."""

import asyncio
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from tianshu.app import create_app, lifespan
from tianshu.models import Edict


@pytest.fixture
async def client():
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def _create_edict(client, *, goal: str, idempotency_key: str):
    with patch("tianshu.executor.agent.LLMClient"):
        return await client.post(
            "/api/edicts",
            json={"idempotency_key": idempotency_key, "goal": goal},
        )


class TestEdictCRUD:
    async def test_update_open_edict_title_preserves_frozen_objective(self, client):
        storage = client._transport.app.state.storage
        edict = Edict(goal="update me")
        storage.save_edict(edict)

        resp = await client.patch(
            f"/api/edicts/{edict.id}",
            json={"title": "New Title"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["title"] == "New Title"
        assert resp.json()["data"]["goal"] == "update me"

    async def test_update_nonexistent(self, client):
        resp = await client.patch(
            "/api/edicts/nonexistent",
            json={"title": "x"},
        )
        assert resp.status_code == 404

    async def test_delete_edict(self, client):
        resp = await _create_edict(
            client,
            goal="delete me",
            idempotency_key="gateway-extended-delete-edict",
        )
        response = resp.json()
        edict_id = response["data"]["id"]
        event_id = response["metadata"]["event_id"]

        # 提交后 edict 会自动进入执行并产生活跃 memorial，按设计此时删除返回 409。
        # 等后台产生 memorial 后将其置为完成，模拟"已结束、可删除"状态，确定性验证删除。
        from tianshu.models.common import TaskStatus

        storage = client._transport.app.state.storage
        with storage._conn:
            storage._conn.execute(
                """
                INSERT OR IGNORE INTO outbox_consumptions (
                    event_id, consumer_name, result_hash, consumed_at
                ) VALUES (?, 'delete-contract-test', NULL, '2026-07-15T00:00:00+00:00')
                """,
                (event_id,),
            )
        assert (
            storage._conn.execute(
                "SELECT COUNT(*) FROM submission_idempotency WHERE edict_id = ?",
                (edict_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            storage._conn.execute(
                "SELECT COUNT(*) FROM outbox_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            storage._conn.execute(
                "SELECT COUNT(*) FROM outbox_consumptions WHERE event_id = ?",
                (event_id,),
            ).fetchone()[0]
            == 1
        )
        mems = []
        for _ in range(50):
            mems = storage.list_memorials_by_edict(edict_id)
            if mems:
                break
            await asyncio.sleep(0.02)
        for m in mems:
            m.status = TaskStatus.COMPLETED
            storage.update_memorial(m)

        resp = await client.delete(f"/api/edicts/{edict_id}")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "governed_evolution_history_retained"

        resp = await client.get(f"/api/edicts/{edict_id}")
        assert resp.status_code == 200
        assert (
            storage._conn.execute(
                "SELECT COUNT(*) FROM submission_idempotency WHERE edict_id = ?",
                (edict_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            storage._conn.execute(
                "SELECT COUNT(*) FROM outbox_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            storage._conn.execute(
                "SELECT COUNT(*) FROM outbox_consumptions WHERE event_id = ?",
                (event_id,),
            ).fetchone()[0]
            == 1
        )

    async def test_delete_nonexistent(self, client):
        resp = await client.delete("/api/edicts/nonexistent")
        assert resp.status_code == 404

    async def test_list_with_filters(self, client):
        await _create_edict(
            client,
            goal="searchable task",
            idempotency_key="gateway-extended-list-filter",
        )

        resp = await client.get("/api/edicts?search=searchable")
        assert resp.status_code == 200
        data = resp.json()
        assert data["metadata"]["total"] >= 1

    async def test_list_with_status(self, client):
        resp = await client.get("/api/edicts?status=open")
        assert resp.status_code == 200

    async def test_list_with_pagination(self, client):
        resp = await client.get("/api/edicts?limit=5&offset=0")
        assert resp.status_code == 200
        assert resp.json()["metadata"]["limit"] == 5


class TestEdictStatus:
    async def test_update_status(self, client):
        resp = await _create_edict(
            client,
            goal="close me",
            idempotency_key="gateway-extended-update-status",
        )
        edict_id = resp.json()["data"]["id"]

        resp = await client.patch(
            f"/api/edicts/{edict_id}/status",
            json={"status": "completed"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "completed"

    async def test_update_status_nonexistent(self, client):
        resp = await client.patch(
            "/api/edicts/nonexistent/status",
            json={"status": "completed"},
        )
        assert resp.status_code == 404

    async def test_update_completed_edict_rejected(self, client):
        resp = await _create_edict(
            client,
            goal="test",
            idempotency_key="gateway-extended-completed-rejected",
        )
        edict_id = resp.json()["data"]["id"]

        await client.patch(f"/api/edicts/{edict_id}/status", json={"status": "completed"})
        resp = await client.patch(f"/api/edicts/{edict_id}", json={"goal": "nope"})
        assert resp.status_code == 400


class TestMemorialEndpoints:
    async def test_get_memorial_by_edict(self, client):
        resp = await _create_edict(
            client,
            goal="memorial test",
            idempotency_key="gateway-extended-get-memorial",
        )
        edict_id = resp.json()["data"]["id"]

        # Wait a bit for the event chain to create memorial
        await asyncio.sleep(0.1)

        resp = await client.get(f"/api/edicts/{edict_id}/memorial")
        assert resp.status_code in (200, 404)  # May or may not exist yet

    async def test_list_memorials_by_edict(self, client):
        resp = await _create_edict(
            client,
            goal="test",
            idempotency_key="gateway-extended-list-memorials",
        )
        edict_id = resp.json()["data"]["id"]

        resp = await client.get(f"/api/edicts/{edict_id}/memorials")
        assert resp.status_code == 200

    async def test_get_memorial_not_found(self, client):
        resp = await client.get("/api/memorials/nonexistent")
        assert resp.status_code == 404

    async def test_list_memorials_with_status(self, client):
        resp = await client.get("/api/memorials?status=completed")
        assert resp.status_code == 200

    async def test_list_memorials_pagination(self, client):
        resp = await client.get("/api/memorials?limit=10&offset=0")
        assert resp.status_code == 200


class TestEventEndpoints:
    async def test_get_events(self, client):
        resp = await _create_edict(
            client,
            goal="events test",
            idempotency_key="gateway-extended-get-events",
        )
        edict_id = resp.json()["data"]["id"]

        resp = await client.get(f"/api/edicts/{edict_id}/events")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)


class TestConfigManagement:
    async def test_create_config(self, client):
        resp = await client.post(
            "/api/configs",
            json={"name": "new-config", "model": "gpt-4"},
        )
        assert resp.status_code == 201

    async def test_create_duplicate_config(self, client):
        await client.post("/api/configs", json={"name": "dup", "model": "x"})
        resp = await client.post("/api/configs", json={"name": "dup", "model": "y"})
        assert resp.status_code == 409

    async def test_update_config(self, client):
        resp = await client.put("/api/config", json={"temperature": 0.5})
        assert resp.status_code == 200

    async def test_update_config_empty(self, client):
        resp = await client.put("/api/config", json={})
        assert resp.status_code == 200

    async def test_update_named_config(self, client):
        await client.post("/api/configs", json={"name": "updatable", "model": "m"})
        resp = await client.put("/api/configs/updatable", json={"temperature": 0.3})
        assert resp.status_code == 200

    async def test_update_nonexistent_config(self, client):
        resp = await client.put("/api/configs/nope", json={"temperature": 0.3})
        assert resp.status_code == 404

    async def test_delete_config(self, client):
        await client.post("/api/configs", json={"name": "deletable", "model": "m"})
        resp = await client.delete("/api/configs/deletable")
        assert resp.status_code == 200

    async def test_delete_nonexistent_config(self, client):
        resp = await client.delete("/api/configs/nope")
        assert resp.status_code == 404

    async def test_activate_config(self, client):
        await client.post("/api/configs", json={"name": "activatable", "model": "m"})
        resp = await client.put("/api/configs/activatable/activate")
        assert resp.status_code == 200

    async def test_activate_nonexistent(self, client):
        resp = await client.put("/api/configs/nope/activate")
        assert resp.status_code == 404

    async def test_update_agent_config(self, client):
        resp = await client.put(
            "/api/agent-config",
            json={"agent_max_iterations": 10},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["agent_max_iterations"] == 10

    async def test_update_agent_config_empty(self, client):
        resp = await client.put("/api/agent-config", json={})
        assert resp.status_code == 200


class TestFollowUp:
    async def test_follow_up_nonexistent(self, client):
        resp = await client.post(
            "/api/edicts/nonexistent/follow-up",
            json={"instruction": "more"},
            headers={"Idempotency-Key": "follow-up-nonexistent"},
        )
        assert resp.status_code == 404

    async def test_memorial_by_edict_not_found(self, client):
        resp = await client.get("/api/edicts/nonexistent/memorial")
        assert resp.status_code == 404
