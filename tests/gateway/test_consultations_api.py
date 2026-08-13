"""廷议 HTTP 面：落库、刷新可恢复、列表、后台任务兜底（issue #52）。

用真实 Storage（tests/conftest.py::storage）跑 SQL 与迁移，不用桩替身——本次修复的
核心承诺就是"结果不再只活在进程内存里"，桩会把这一点整个绕过去。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from tianshu.consultation.models import ConsultationRequest
from tianshu.consultation.session import ConsultationSession
from tianshu.gateway.api import gateway_router


class _StubSession(ConsultationSession):
    """跳过 LLM：run() 只把最新一轮推到终态，其余落库路径保持真实。"""

    def __init__(self, storage, *, fail: bool = False) -> None:
        self._storage = storage
        self._notifier = None
        self._sessions = {}
        self._fail = fail

    async def run(self, consultation_id: str, *, usage_context=None):
        record = self.get(consultation_id)
        status = "failed" if self._fail else "completed"
        error = "downstream unavailable" if self._fail else None
        for round_ in record.rounds:
            if round_.status not in {"pending", "running"}:
                continue
            round_.status = status
            round_.synthesis = None if self._fail else "综合意见"
            round_.proposal = None if self._fail else "票拟建议"
            round_.error = error
            round_.completed_at = datetime.now(UTC)
            self._persist_round(round_)
        record.status = status
        record.error = error
        self._persist(record)
        return record


@pytest.fixture
def app(storage):
    application = FastAPI()
    application.state.consultation = _StubSession(storage)
    application.state.consultation_tasks = set()
    application.include_router(gateway_router, prefix="/api")
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def _create(client, topic="如何在 ai 时代具备个人竞争力?"):
    resp = client.post(
        "/api/consultations",
        json={"topic": topic, "persona_ids": ["neige"]},
    )
    assert resp.status_code == 202
    return resp.json()["data"]["id"]


class TestConsultationPersistence:
    def test_consultation_survives_a_fresh_client(self, client, storage):
        """刷新页面 = 一个全新的 HTTP 客户端；结果必须仍能按 id 取回。"""
        consultation_id = _create(client)

        row = storage.get_consultation(consultation_id)
        assert row is not None
        assert row.request.topic == "如何在 ai 时代具备个人竞争力?"

        resp = client.get(f"/api/consultations/{consultation_id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == consultation_id

    def test_list_endpoint_returns_history_newest_first(self, client):
        first = _create(client, topic="旧议题")
        second = _create(client, topic="新议题")

        data = client.get("/api/consultations").json()["data"]
        ids = [item["id"] for item in data]
        assert ids.index(second) < ids.index(first)

    def test_list_filters_by_status(self, client, app, storage):
        _create(client)
        app.state.consultation = _StubSession(storage, fail=True)
        failed_id = _create(client)

        data = client.get("/api/consultations", params={"status": "failed"}).json()["data"]
        assert [item["id"] for item in data] == [failed_id]
        assert data[0]["error"] == "downstream unavailable"

    def test_unknown_consultation_is_404(self, client):
        assert client.get("/api/consultations/nope").status_code == 404


class TestBackgroundTaskSafety:
    async def test_escaping_exception_marks_failed_instead_of_hanging(self, storage):
        """后台任务抛异常时必须落到 failed——否则前端永远轮询一个 running。"""

        class _ExplodingSession(_StubSession):
            async def run(self, consultation_id: str, *, usage_context=None):
                raise RuntimeError("boom")

        application = FastAPI()
        session = _ExplodingSession(storage)
        application.state.consultation = session
        application.state.consultation_tasks = set()
        application.include_router(gateway_router, prefix="/api")

        pending = session.create_pending(ConsultationRequest(topic="t", persona_ids=["neige"]))

        from tianshu.gateway.api import _spawn_consultation

        _spawn_consultation(application, session, pending.id)
        await asyncio.gather(*application.state.consultation_tasks, return_exceptions=True)
        await asyncio.sleep(0)  # done callback 经 call_soon 调度，让它落盘

        record = session.get(pending.id)
        assert record.status == "failed"
        assert "RuntimeError: boom" in record.error

    def test_restart_marks_orphaned_consultations_failed(self, storage):
        session = _StubSession(storage)
        pending = session.create_pending(ConsultationRequest(topic="t", persona_ids=["neige"]))

        assert storage.mark_stale_consultations_failed("interrupted by server restart") == 1

        record = session.get(pending.id)
        assert record.status == "failed"
        assert record.error == "interrupted by server restart"


class TestRealAssembly:
    """走 create_app + lifespan 的真实装配链——桩 app 验不到 wire_consultation。"""

    async def test_consultation_persists_through_the_real_app(self):
        from tianshu.app import create_app, lifespan

        app = create_app()
        async with lifespan(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # persona_ids 指向不存在的官员：无人可奏对，不触发任何 LLM 调用，
                # 廷议会走到 failed——正是修复前会白屏报 completed 的那条路径。
                resp = await client.post(
                    "/api/consultations",
                    json={"topic": "真实装配链验证", "persona_ids": ["nobody"]},
                )
                assert resp.status_code == 202
                consultation_id = resp.json()["data"]["id"]

                for _ in range(50):
                    detail = await client.get(f"/api/consultations/{consultation_id}")
                    assert detail.status_code == 200
                    if detail.json()["data"]["status"] in {"completed", "failed"}:
                        break
                    await asyncio.sleep(0.02)

                data = detail.json()["data"]
                assert data["status"] == "failed"
                assert data["error"]  # 归因非空，前端不再只能显示"请稍后重试"

                listed = await client.get("/api/consultations")
                assert consultation_id in [item["id"] for item in listed.json()["data"]]

            # 真实装配把记录写进了库，而不是只活在进程内存里
            assert app.state.storage.get_consultation(consultation_id) is not None


class TestMultiRoundApi:
    """追问与裁决的 HTTP 面（issue #55）。"""

    def test_append_round_creates_a_new_round(self, client, storage):
        consultation_id = _create(client)

        resp = client.post(
            f"/api/consultations/{consultation_id}/rounds",
            json={"prompt": "户部单独说说钱", "participant_ids": ["hubu"]},
        )

        assert resp.status_code == 202
        assert resp.json()["data"]["round_index"] == 1
        rounds = storage.list_consultation_rounds(consultation_id)
        assert [r.round_index for r in rounds] == [0, 1]
        assert rounds[1].prompt == "户部单独说说钱"
        assert rounds[1].participant_ids == ["hubu"]

    def test_append_round_without_names_inherits_first_round_roster(self, client, storage):
        consultation_id = _create(client)

        client.post(f"/api/consultations/{consultation_id}/rounds", json={"prompt": "再议"})

        rounds = storage.list_consultation_rounds(consultation_id)
        assert rounds[1].participant_ids == ["neige"]  # 沿用首轮名单

    def test_append_round_rejects_empty_prompt(self, client):
        consultation_id = _create(client)
        resp = client.post(
            f"/api/consultations/{consultation_id}/rounds",
            json={"prompt": "   "},
        )
        assert resp.status_code == 422

    def test_append_round_on_unknown_consultation_is_404(self, client):
        resp = client.post("/api/consultations/nope/rounds", json={"prompt": "追问"})
        assert resp.status_code == 404

    def test_verdict_is_recorded_and_returned(self, client, storage):
        consultation_id = _create(client)

        resp = client.put(
            f"/api/consultations/{consultation_id}/verdict",
            json={"verdict": "准奏，但须季度复核。"},
        )

        assert resp.status_code == 200
        assert resp.json()["data"]["verdict"] == "准奏，但须季度复核。"
        assert resp.json()["data"]["verdict_at"]
        assert storage.get_consultation(consultation_id).verdict == "准奏，但须季度复核。"

    def test_verdict_rejects_empty_text(self, client):
        consultation_id = _create(client)
        resp = client.put(
            f"/api/consultations/{consultation_id}/verdict",
            json={"verdict": "  "},
        )
        assert resp.status_code == 422

    def test_verdict_on_unknown_consultation_is_404(self, client):
        resp = client.put("/api/consultations/nope/verdict", json={"verdict": "准奏"})
        assert resp.status_code == 404

    def test_detail_returns_rounds(self, client):
        consultation_id = _create(client)
        client.post(f"/api/consultations/{consultation_id}/rounds", json={"prompt": "再议"})

        data = client.get(f"/api/consultations/{consultation_id}").json()["data"]

        assert [r["round_index"] for r in data["rounds"]] == [0, 1]
        assert data["rounds"][0]["prompt"] == "如何在 ai 时代具备个人竞争力?"
