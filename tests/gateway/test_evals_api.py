"""evals_api 只读端点(迭代 2「证明」)。

治理边界锚点:评测跑批只在 CLI,HTTP 面不得出现触发端点(POST /evals/*)。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.gateway.evals_api import evals_router
from tianshu.models import Memorial, TaskStatus
from tianshu.models.edict import Edict


@pytest.fixture
def client(storage):
    app = FastAPI()
    app.state.storage = storage
    app.include_router(evals_router, prefix="/api")
    return TestClient(app)


def _seed_run(storage, run_id: str = "r1") -> None:
    storage.save_platform_eval_run(
        {
            "id": run_id,
            "eval_set_name": "reg",
            "eval_set_fingerprint": "fp",
            "target": "/repo@abc",
            "fitness": {"score": 0.5},
            "stats": {"total": 1},
            "goal_results": [
                {
                    "instruction": "g",
                    "status": "failed",
                    "error": "429",
                    "failure_reason": "agent_error.provider_capacity_or_rate_limit",
                    "cost": 0.0,
                }
            ],
            "n": 1,
            "truncated": False,
            "delta_vs_prev": None,
            "created_at": "2026-07-08T00:00:00+00:00",
        }
    )


class TestEvalsApi:
    def test_list_runs(self, client, storage):
        _seed_run(storage)
        resp = client.get("/api/evals/runs")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data[0]["id"] == "r1"
        assert "goal_results" not in data[0]  # brief 视图

    def test_get_run_with_failure_distribution(self, client, storage):
        _seed_run(storage)
        resp = client.get("/api/evals/runs/r1")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["goal_results"][0]["failure_reason"] == (
            "agent_error.provider_capacity_or_rate_limit"
        )
        assert data["failure_distribution"] == [
            {"reason": "agent_error.provider_capacity_or_rate_limit", "count": 1}
        ]

    def test_get_run_404(self, client):
        assert client.get("/api/evals/runs/ghost").status_code == 404

    def test_list_sets(self, client, storage):
        storage.save_eval_set("reg", ["g1"])
        data = client.get("/api/evals/sets").json()["data"]
        assert data[0]["name"] == "reg"

    def test_failure_distribution_from_main_db(self, client, storage):
        e = Edict(goal="g")
        storage.save_edict(e)
        storage.save_memorial(
            Memorial(edict_id=e.id, status=TaskStatus.FAILED, error="401 unauthorized")
        )
        data = client.get("/api/evals/failure-distribution").json()["data"]
        assert data[0]["reason"] == "agent_error.provider_auth_or_access"
        assert data[0]["count"] == 1

    def test_no_write_endpoints_exposed(self, client):
        """治理边界:评测跑批(花钱的重活)不得有 HTTP 触发面。"""
        assert client.post("/api/evals/runs").status_code in (404, 405)
        assert client.post("/api/evals/run").status_code in (404, 405)
