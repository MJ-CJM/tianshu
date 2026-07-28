"""导入预览端点 + 技能复制助手测试。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tianshu.bootstrap.wiring_skills as wiring_skills
from tianshu.gateway.personas_api import _install_imported_skills, personas_router


def _write(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def hermes_home(tmp_path):
    home = tmp_path / ".hermes"
    _write(home / "SOUL.md", "---\nname: 赫尔墨斯\n---\n\n务实的工程助手。")
    _write(home / "config.yaml", "model:\n  default: anthropic/claude-opus-4\n")
    _write(home / "skills" / "ci" / "SKILL.md", "---\nname: ci-runner\ndescription: run CI\n---\n# CI")
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
        r = client.post("/personas/import/preview", json={"source": "hermes", "path": "/nonexistent-xyz"})
        assert r.status_code == 400


class TestInstallImportedSkills:
    def test_copies_skill_and_returns_name(self, tmp_path, monkeypatch):
        target = tmp_path / "user-skills"
        monkeypatch.setattr(wiring_skills, "runtime_skills_target", lambda: target)
        src = tmp_path / "src" / "mytool"
        _write(src / "SKILL.md", "---\nname: my-tool\ndescription: x\n---\n# My")
        _write(src / "references" / "ref.md", "ref")

        names = _install_imported_skills([str(src)])
        assert names == ["my-tool"]
        assert (target / "my-tool" / "SKILL.md").is_file()
        assert (target / "my-tool" / "references" / "ref.md").is_file()  # 整目录复制

    def test_existing_skill_still_associated_not_recopied(self, tmp_path, monkeypatch):
        target = tmp_path / "user-skills"
        (target / "dup").mkdir(parents=True)
        monkeypatch.setattr(wiring_skills, "runtime_skills_target", lambda: target)
        src = tmp_path / "src" / "dup"
        _write(src / "SKILL.md", "---\nname: dup\n---\n# Dup")
        names = _install_imported_skills([str(src)])
        assert names == ["dup"]  # 已存在仍关联

    def test_skips_invalid_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring_skills, "runtime_skills_target", lambda: tmp_path / "t")
        src = tmp_path / "src" / "bad"
        _write(src / "SKILL.md", "---\nname: 'Invalid Name With Spaces'\n---\n# bad")
        assert _install_imported_skills([str(src)]) == []  # 非法名跳过,失败安全

    def test_skips_missing_skill_md(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wiring_skills, "runtime_skills_target", lambda: tmp_path / "t")
        assert _install_imported_skills([str(tmp_path / "no-skill-here")]) == []
