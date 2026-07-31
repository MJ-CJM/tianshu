"""/api/personas 与 /api/departments 端点集成测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from tianshu.app import create_app, lifespan
from tianshu.bus.event_bus import EventBus
from tianshu.gateway.personas_api import trigger_profile_synthesis
from tianshu.models.events import make_event


@pytest.fixture
async def client():
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_list_departments(client):
    # 实测：_seed_departments() 在空库首次迁移时写入 6 个内建部门（不含 "court"）
    resp = await client.get("/api/departments")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    ids = [d["id"] for d in body["data"]]
    assert "bingbu" in ids  # 内建部门之一（注意：这是部门 id，不是 persona id）


async def test_list_personas_seeds_six_default_departments(client):
    # G1.5 v6 迁移：全新库 seed 恰好六个内建部门（0006_seed_default_personas）
    resp = await client.get("/api/personas")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    ids = sorted(p["id"] for p in body["data"])
    assert ids == ["bingbu", "ducha", "hubu", "neige", "tongzheng", "wenyuan"]


async def test_create_update_delete_persona_roundtrip(client):
    # 部门取自内建列表首项，避免硬编码部门 id
    depts = (await client.get("/api/departments")).json()["data"]
    dept_id = depts[0]["id"]

    # body 字段以 personas_api.py create_persona 的入参解析为准：
    # id/name/department 必填，title/tools_allowed 等为可选扩展字段
    create_body = {
        "id": "test-persona-1",
        "name": "测试官员",
        "department": dept_id,
        "title": "test-title",
        "tools_allowed": ["read_file"],
    }
    resp = await client.post("/api/personas", json=create_body)
    assert resp.status_code == 201
    data = resp.json()["data"]
    pid = data["id"]
    assert pid == "test-persona-1"
    assert data["name"] == "测试官员"
    assert data["department"] == dept_id
    assert data["title"] == "test-title"
    assert data["tools_allowed"] == ["read_file"]

    # personas_api.py 没有单条 GET /personas/{id} 详情路由（仅 list/create/update/delete/
    # metrics/profile 等子路径），往返验证改用 list 断言存在/消失
    ids_after_create = [p["id"] for p in (await client.get("/api/personas")).json()["data"]]
    assert pid in ids_after_create

    resp = await client.put(f"/api/personas/{pid}", json={"title": "updated-title"})
    assert resp.status_code == 200
    assert resp.json()["data"]["title"] == "updated-title"

    resp = await client.delete(f"/api/personas/{pid}")
    assert resp.status_code == 200
    ids_after_delete = [p["id"] for p in (await client.get("/api/personas")).json()["data"]]
    assert pid not in ids_after_delete


async def test_create_persona_missing_fields_rejected(client):
    resp = await client.post("/api/personas", json={})
    assert resp.status_code == 400


async def test_create_persona_rejects_direct_imported_skill_install(client):
    depts = (await client.get("/api/departments")).json()["data"]
    dept_id = depts[0]["id"]
    body = {
        "id": "test-persona-skill-bypass",
        "name": "技能旁路测试",
        "department": dept_id,
        "import_skill_paths": ["/tmp/unreviewed-skill"],
    }

    resp = await client.post("/api/personas", json=body)

    assert resp.status_code == 409
    assert "governed candidate flow" in resp.json()["detail"]
    ids = [p["id"] for p in (await client.get("/api/personas")).json()["data"]]
    assert body["id"] not in ids


async def test_create_persona_duplicate_conflict(client):
    depts = (await client.get("/api/departments")).json()["data"]
    dept_id = depts[0]["id"]
    body = {"id": "test-persona-dup", "name": "官员甲", "department": dept_id}
    resp = await client.post("/api/personas", json=body)
    assert resp.status_code == 201

    resp = await client.post("/api/personas", json=body)
    assert resp.status_code == 409


async def test_persona_not_found_paths(client):
    # personas_api.py 无单条 GET 详情路由；update/delete 对不存在 persona_id 的 404 校验
    # 在此覆盖 "detail_and_404" 场景的等价语义
    resp = await client.put("/api/personas/no-such-persona", json={"title": "x"})
    assert resp.status_code == 404
    resp = await client.delete("/api/personas/no-such-persona")
    assert resp.status_code == 404


async def test_persona_templates_list_and_detail(client):
    resp = await client.get("/api/persona-templates")
    assert resp.status_code == 200
    data = resp.json()["data"]
    if data:  # 有模板时抽第一个验证详情
        tid = data[0]["templates"][0]["id"]
        assert (await client.get(f"/api/persona-templates/{tid}")).status_code == 200


async def test_persona_metrics_and_profile(client):
    # metrics 端点不校验 persona 是否存在（纯统计查询），任意 id 均 200
    depts = (await client.get("/api/departments")).json()["data"]
    dept_id = depts[0]["id"]
    create_body = {"id": "test-persona-profile", "name": "测试官员", "department": dept_id}
    resp = await client.post("/api/personas", json=create_body)
    assert resp.status_code == 201
    pid = create_body["id"]

    assert (await client.get(f"/api/personas/{pid}/metrics")).status_code == 200

    resp = await client.get(f"/api/personas/{pid}/profile")
    assert resp.status_code == 200  # 实测：无 PROFILE.md 文件时返回 200 + exists=False
    body = resp.json()["data"]
    assert body["exists"] is False
    assert body["persona_id"] == pid

    # profile 端点会校验 persona 是否存在；不存在的 persona_id 返回 404
    resp = await client.get("/api/personas/no-such-persona/profile")
    assert resp.status_code == 404


async def test_create_department_missing_fields_rejected(client):
    resp = await client.post("/api/departments", json={})
    assert resp.status_code == 400


async def test_create_update_delete_department_roundtrip(client):
    create_body = {"id": "test-dept-1", "name": "测试司", "description": "for test"}
    resp = await client.post("/api/departments", json=create_body)
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["id"] == "test-dept-1"
    assert data["name"] == "测试司"
    assert data["description"] == "for test"

    resp = await client.put("/api/departments/test-dept-1", json={"name": "改名司"})
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "改名司"

    resp = await client.delete("/api/departments/test-dept-1")
    assert resp.status_code == 200
    remaining_ids = [d["id"] for d in (await client.get("/api/departments")).json()["data"]]
    assert "test-dept-1" not in remaining_ids


async def test_department_not_found_paths(client):
    resp = await client.put("/api/departments/ghost-dept", json={"name": "x"})
    assert resp.status_code == 404
    resp = await client.delete("/api/departments/ghost-dept")
    assert resp.status_code == 404


async def test_two_synthesis_streams_for_same_persona_receive_local_events():
    event_bus = EventBus()

    class _Synthesizer:
        def __init__(self) -> None:
            self.calls = 0
            self.both_started = asyncio.Event()

        async def run(self, persona_id: str, *, trigger_source: str) -> None:
            assert trigger_source == "api_manual"
            self.calls += 1
            if self.calls == 2:
                self.both_started.set()
            await self.both_started.wait()
            await event_bus.emit(
                make_event(
                    "profile.synthesis.completed",
                    payload={"persona_id": persona_id},
                )
            )

    synthesizer = _Synthesizer()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                persona_loader=SimpleNamespace(get=lambda persona_id: object()),
                profile_synthesizer=synthesizer,
                event_bus=event_bus,
            )
        )
    )
    first = await trigger_profile_synthesis("bingbu", request)
    second = await trigger_profile_synthesis("bingbu", request)

    async def consume(response) -> str:
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    first_body, second_body = await asyncio.wait_for(
        asyncio.gather(consume(first), consume(second)),
        timeout=1,
    )

    assert synthesizer.calls == 2
    assert "event: profile.synthesis.completed" in first_body
    assert "event: profile.synthesis.completed" in second_body
    assert all(
        event_bus.local_subscriber_count(event_type) == 0
        for event_type in (
            "profile.synthesis.started",
            "profile.synthesis.completed",
            "profile.synthesis.degraded",
            "profile.synthesis.failed",
            "profile.synthesis.skipped",
        )
    )
