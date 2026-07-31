"""Memory maintenance API regression tests."""

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.gateway.memory_api import memory_router
from tianshu.memory.models import MemoryEntry


def test_compact_memory_compares_datetime_timestamps_without_500() -> None:
    app = FastAPI()
    app.state.memory_manager = SimpleNamespace(
        list_by_persona=lambda _persona_id, limit: [
            MemoryEntry(
                persona_id="bingbu",
                content="fresh observation",
                created_at=datetime.now(UTC),
            )
        ]
    )
    app.include_router(memory_router, prefix="/api")

    with TestClient(app) as client:
        response = client.post(
            "/api/memory/compact",
            json={"persona_id": "bingbu", "max_age_days": 7},
        )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "skipped"
