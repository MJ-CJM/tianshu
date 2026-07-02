"""list_personas tool 单元测试。"""

from __future__ import annotations

import pytest

from tianshu.tools.persona_query import register_list_personas
from tianshu.tools.registry import ToolRegistry


def _make_persona(
    pid: str,
    department: str,
    *,
    name: str | None = None,
    title: str | None = None,
    llm: str | None = None,
    can_delegate: bool = False,
    delegates_to: list | None = None,
) -> dict:
    return {
        "id": pid,
        "name": name or pid,
        "department": department,
        "title": title,
        "tools_allowed": [],
        "tools_denied": [],
        "skills_allowed": [],
        "tool_tier_max": 0,
        "can_delegate": can_delegate,
        "delegates_to": delegates_to or [],
        "llm_config_name": llm,
        "soul_path": f"~/.tianshu/personas/{pid}/SOUL.md",
        "role_path": f"~/.tianshu/personas/{pid}/ROLE.md",
    }


@pytest.fixture
def registry_with_personas(storage, monkeypatch):
    """注册 list_personas + mock storage.list_personas 返回固定 4 个 persona。"""
    fixture = [
        _make_persona("wym", "neige", title="大学士", llm="qwen", can_delegate=True),
        _make_persona("ys", "ducha", title="御史", llm="mimo_2"),
        _make_persona("wy", "wenyuan", llm="mimo_2"),
        _make_persona("tbh", "wenyuan", llm="mimo_2"),
    ]
    monkeypatch.setattr(storage, "list_personas", lambda: list(fixture))
    r = ToolRegistry()
    register_list_personas(r, storage=storage)
    return r, fixture


@pytest.mark.asyncio
async def test_list_personas_returns_db_rows_not_directories(registry_with_personas):
    """核心需求：返回 DB persona 实例，不是文件系统模板。"""
    r, fixture = registry_with_personas
    result = await r.execute("list_personas", {})
    assert result.is_error is False
    assert result.details["count"] == 4
    ids = [p["id"] for p in result.details["personas"]]
    assert set(ids) == {"wym", "ys", "wy", "tbh"}
    # 文本里每行都该带 id
    for pid in ids:
        assert pid in result.content


@pytest.mark.asyncio
async def test_list_personas_filter_by_department(registry_with_personas):
    r, _ = registry_with_personas
    result = await r.execute("list_personas", {"department": "wenyuan"})
    ids = [p["id"] for p in result.details["personas"]]
    assert set(ids) == {"wy", "tbh"}
    assert "wym" not in result.content


@pytest.mark.asyncio
async def test_list_personas_filter_only_can_delegate(registry_with_personas):
    r, _ = registry_with_personas
    result = await r.execute("list_personas", {"only_can_delegate": True})
    ids = [p["id"] for p in result.details["personas"]]
    assert ids == ["wym"]


@pytest.mark.asyncio
async def test_list_personas_empty_db_explicit_warning(storage, monkeypatch):
    """空 DB 时返回明确说明 + 阻止 LLM 编造名册的提示。"""
    monkeypatch.setattr(storage, "list_personas", lambda: [])
    r = ToolRegistry()
    register_list_personas(r, storage=storage)
    result = await r.execute("list_personas", {})
    assert result.is_error is False
    assert result.details["count"] == 0
    # 必须显式警告 LLM 别去翻代码模板
    assert "DB" in result.content
    assert "为空" in result.content
    assert "勿凭推测" in result.content or "勿" in result.content


@pytest.mark.asyncio
async def test_list_personas_filter_unknown_department(registry_with_personas):
    """过滤到不存在的部门 → 返回 0，提示 DB 中无此 department。"""
    r, _ = registry_with_personas
    result = await r.execute("list_personas", {"department": "bingbu"})
    assert result.details["count"] == 0
    assert "bingbu" in result.content


@pytest.mark.asyncio
async def test_list_personas_exposes_llm_binding(registry_with_personas):
    """LLM 配置绑定要透出来，让助手能告知用户'谁用什么模型'。"""
    r, _ = registry_with_personas
    result = await r.execute("list_personas", {})
    assert "qwen" in result.content
    assert "mimo_2" in result.content


@pytest.mark.asyncio
async def test_list_personas_schema_correct(registry_with_personas):
    r, _ = registry_with_personas
    defn = r.get_definition("list_personas")
    assert defn.name == "list_personas"
    props = defn.parameters["properties"]
    assert "department" in props
    assert "only_can_delegate" in props
    assert defn.parameters["required"] == []
    # T0 只读，不进 hook 链
    assert defn.tier == 0


@pytest.mark.asyncio
async def test_list_personas_exposes_role_path(registry_with_personas):
    """role_path 必须出现在 details，让 LLM 能 read_file 查 ROLE.md 细看职责。"""
    r, _ = registry_with_personas
    result = await r.execute("list_personas", {})
    for p in result.details["personas"]:
        assert "role_path" in p
        assert p["role_path"] is not None
        assert "ROLE.md" in p["role_path"]


@pytest.mark.asyncio
async def test_list_personas_description_guides_submit_edict_chain(registry_with_personas):
    """description 必须明确告诉 LLM：submit_edict 未指定指派人时要先调本工具。"""
    r, _ = registry_with_personas
    defn = r.get_definition("list_personas")
    assert "submit_edict" in defn.description
    assert "未指定" in defn.description
    assert "role_path" in defn.description  # 提示 LLM 用 read_file 看细节
