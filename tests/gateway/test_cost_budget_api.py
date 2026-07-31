"""Cost budget API contracts for scoped budgets and explicit reset times."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tianshu.app import create_app, lifespan


@pytest.fixture
async def client():
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


async def test_budget_api_round_trips_submitter_scope_and_reset_at(client) -> None:
    reset_at = "2099-01-02T03:04:05+00:00"

    response = await client.put(
        "/api/cost/budget",
        json={
            "scope": "submitter:user:owner",
            "budget_cny": 12.5,
            "period": "monthly",
            "reset_at": reset_at,
        },
    )
    status = await client.get(
        "/api/cost/budget",
        params={"scope": "submitter:user:owner"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["scope"] == "submitter:user:owner"
    assert response.json()["data"]["period"] == "monthly"
    assert response.json()["data"]["reset_at"] == "2099-01-02T03:04:05Z"
    assert status.status_code == 200
    assert status.json()["data"]["scope"] == "submitter:user:owner"
    assert status.json()["data"]["reset_at"] == "2099-01-02T03:04:05Z"


@pytest.mark.parametrize(
    "payload",
    [
        {"scope": "", "budget_cny": 1, "period": "monthly"},
        {"scope": "unknown", "budget_cny": 1, "period": "monthly"},
        {"scope": "edict:", "budget_cny": 1, "period": "monthly"},
        {"scope": "global", "budget_cny": 0, "period": "monthly"},
        {"scope": "global", "budget_cny": -1, "period": "monthly"},
        {"scope": "global", "budget_cny": 1, "period": "quarterly"},
        {
            "scope": "global",
            "budget_cny": 1,
            "period": "daily",
            "reset_at": "2099-01-02T03:04:05",
        },
    ],
)
async def test_budget_api_rejects_invalid_contracts(client, payload) -> None:
    response = await client.put("/api/cost/budget", json=payload)

    assert response.status_code == 422
