"""COURT.md 的 overlay 写入与冻结 reset 语义。

冻结契约（G1.5）：COURT 变更只写 data overlay；reset = 删除 overlay
override，读取按 precedence 自然回退到当前 packaged 资源；禁止把
packaged bytes 复制进 overlay。packaged 树在任何操作后 digest 不变。
"""

import hashlib
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from tianshu.app import create_app, lifespan
from tianshu.resources import catalog


def _packaged_court_bytes() -> bytes:
    node = catalog.persona_defaults() / "court" / "COURT.md"
    return node.read_bytes()


def _personas_family_digest() -> str:
    return catalog.package_digest(("personas",))["personas"]


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TIANSHU_RUNTIME_PERSONAS_DIR", str(tmp_path / "runtime-personas"))
    monkeypatch.setenv("TIANSHU_MEMORY_DIR", str(tmp_path / "memory"))
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app


async def test_court_put_writes_overlay_only_and_get_prefers_overlay(client):
    c, app = client
    packaged_before = _packaged_court_bytes()
    digest_before = _personas_family_digest()

    resp = await c.put(
        "/api/system-prompt/files/court/COURT.md", json={"content": "# overlay court v1"}
    )
    assert resp.status_code == 200

    # packaged 默认零变更
    assert _packaged_court_bytes() == packaged_before
    assert _personas_family_digest() == digest_before
    # override 落在 runtime overlay
    runtime_dir: Path = app.state.runtime_personas_dir
    override = runtime_dir / "court" / "COURT.md"
    assert override.is_file()
    assert override.read_text(encoding="utf-8") == "# overlay court v1"
    # 读取 overlay 优先
    got = await c.get("/api/system-prompt/files/court/COURT.md")
    assert got.status_code == 200
    assert got.json()["data"]["content"] == "# overlay court v1"


async def test_court_reset_removes_override_and_falls_back_to_packaged(client):
    c, app = client
    packaged = _packaged_court_bytes().decode("utf-8")
    digest_before = _personas_family_digest()
    runtime_dir: Path = app.state.runtime_personas_dir
    override = runtime_dir / "court" / "COURT.md"

    # 1) override 不存在时 reset 幂等（两次均成功，无 override 出现）
    for _ in range(2):
        resp = await c.post("/api/system-prompt/files/court/COURT.md/reset")
        assert resp.status_code == 200
        assert not override.exists(), "reset 不得把 packaged bytes 复制进 overlay"

    # 2) fallback 内容 = 当前 packaged 资源
    got = await c.get("/api/system-prompt/files/court/COURT.md")
    assert got.json()["data"]["content"] == packaged

    # 3) 覆盖后 reset 删除 override 并回退
    await c.put("/api/system-prompt/files/court/COURT.md", json={"content": "# tmp"})
    assert override.is_file()
    resp = await c.post("/api/system-prompt/files/court/COURT.md/reset")
    assert resp.status_code == 200
    assert not override.exists()
    got = await c.get("/api/system-prompt/files/court/COURT.md")
    assert got.json()["data"]["content"] == packaged

    # 4) 之后可重新覆盖（precedence 恢复）
    await c.put("/api/system-prompt/files/court/COURT.md", json={"content": "# again"})
    got = await c.get("/api/system-prompt/files/court/COURT.md")
    assert got.json()["data"]["content"] == "# again"

    # 全程 packaged digest 不变
    assert _personas_family_digest() == digest_before


async def test_list_prompt_files_reports_overlay_target_for_overridden_court(client):
    c, app = client
    await c.put("/api/system-prompt/files/court/COURT.md", json={"content": "# o"})
    resp = await c.get("/api/system-prompt/files")
    rows = [
        r
        for r in resp.json()["data"]["files"]
        if r["persona_id"] == "court" and r["filename"] == "COURT.md"
    ]
    assert rows, "court/COURT.md 必须出现在清单中"
    runtime_dir: Path = app.state.runtime_personas_dir
    assert rows[0]["path"] == str(runtime_dir / "court" / "COURT.md")


def test_packaged_court_baseline_hash_is_stable() -> None:
    # recon 基线：packaged COURT.md 的当前内容哈希（提醒任何有意变更需同步评审）
    digest = hashlib.sha256(_packaged_court_bytes()).hexdigest()
    assert digest == "ef7057123260d7c057acc4072fa059ace80a787297cc77138842e281830527d2"


async def test_court_override_is_injected_into_prompt_preview(client):
    """CRITICAL 回归锁定：override 必须进入真实 system prompt 构建（build/preview），
    而不是只在文件 GET 中可见。"""
    c, app = client
    marker = "OVERLAY-COURT-MARKER-7f3a"
    resp = await c.put(
        "/api/system-prompt/files/court/COURT.md",
        json={"content": f"# Court Protocol Override\n\n{marker}\n"},
    )
    assert resp.status_code == 200

    preview = await c.get("/api/system-prompt/preview/bingbu")
    assert preview.status_code == 200
    assert marker in preview.text, "COURT override 未注入 prompt 构建——写读分叉"

    reset = await c.post("/api/system-prompt/files/court/COURT.md/reset")
    assert reset.status_code == 200
    preview_after = await c.get("/api/system-prompt/preview/bingbu")
    assert marker not in preview_after.text
    # PromptBuilder._read_file 会剥离 YAML frontmatter，用 packaged 正文特征断言回退
    assert "# 朝廷 (Imperial Court)" in preview_after.text


async def test_court_file_routes_reject_non_court_persona(client):
    """COURT.md 是全局共享层，override 路径固定 court/；非 court persona_id 的
    COURT 写入会产生永远读不到的孤儿 override，必须 400 拒绝。"""
    c, app = client
    put = await c.put("/api/system-prompt/files/bingbu/COURT.md", json={"content": "orphan"})
    assert put.status_code == 400
    reset = await c.post("/api/system-prompt/files/bingbu/COURT.md/reset")
    assert reset.status_code == 400
