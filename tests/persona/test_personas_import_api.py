"""Persona 外部导入只读预览端点测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.gateway.personas_api import personas_router


def _write(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def hermes_home(tmp_path):
    home = tmp_path / ".hermes"
    _write(home / "SOUL.md", "---\nname: 赫尔墨斯\n---\n\n务实的工程助手。")
    _write(home / "config.yaml", "model:\n  default: anthropic/claude-opus-4\n")
    _write(
        home / "skills" / "ci" / "SKILL.md", "---\nname: ci-runner\ndescription: run CI\n---\n# CI"
    )
    return home


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(personas_router)
    return TestClient(app)


class TestPreviewEndpoint:
    def test_preview_hermes(self, client, hermes_home):
        r = client.post(
            "/personas/import/preview", json={"source": "hermes", "path": str(hermes_home)}
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["source"] == "hermes"
        assert data["suggested_name"] == "赫尔墨斯"
        assert data["suggested_model"] == "anthropic/claude-opus-4"
        assert [s["name"] for s in data["skills"]] == ["ci-runner"]
        assert any("memories" in n for n in data["source_notes"])  # 排除项透明

    def test_bad_source_400(self, client):
        r = client.post("/personas/import/preview", json={"source": "grok", "path": "/x"})
        assert r.status_code == 400

    def test_missing_path_and_no_default_400(self, client):
        # 不给 path 且默认目录不存在 → 400(不静默)
        r = client.post(
            "/personas/import/preview", json={"source": "hermes", "path": "/nonexistent-xyz"}
        )
        assert r.status_code == 400
