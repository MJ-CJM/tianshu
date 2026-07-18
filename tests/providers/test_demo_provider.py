"""G1.5 demo provider：精确 opt-in、确定性、零网络、live 永不回退。"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import pytest

from tianshu.config import TianshuSettings
from tianshu.config_manager import ConfigManager, LLMConfigState
from tianshu.executor.orchestrator.audit import parse_audit_response
from tianshu.llm import LLMClient
from tianshu.models.common import AuditResult
from tianshu.models.plan import PlanTask
from tianshu.planner.planner import Planner
from tianshu.providers.demo import DEMO_MARK, DEMO_MODEL, DEMO_PROVIDER, demo_llm_config_state


@pytest.fixture
def no_network(monkeypatch):
    """demo 路径不得调用 LiteLLM，也不得发起任何 socket 连接。"""
    import litellm

    async def _forbidden(**kwargs):  # pragma: no cover - 触发即失败
        raise AssertionError("litellm.acompletion must not be called in demo mode")

    monkeypatch.setattr(litellm, "acompletion", _forbidden)

    original_connect = socket.socket.connect

    def _no_connect(self, address):  # pragma: no cover - 触发即失败
        raise AssertionError(f"socket connect attempted in demo mode: {address}")

    monkeypatch.setattr(socket.socket, "connect", _no_connect)
    yield
    monkeypatch.setattr(socket.socket, "connect", original_connect)


def _demo_client() -> LLMClient:
    return LLMClient(model=DEMO_MODEL, api_key="", provider_name=DEMO_PROVIDER)


def test_demo_requires_exact_opt_in_profile_and_model(monkeypatch):
    monkeypatch.delenv("TIANSHU_STARTUP_PROFILE", raising=False)
    assert TianshuSettings(_env_file=None).startup_profile == "live"
    monkeypatch.setenv("TIANSHU_STARTUP_PROFILE", "demo")
    assert TianshuSettings(_env_file=None).startup_profile == "demo"

    state = demo_llm_config_state()
    assert state.model == DEMO_MODEL
    assert state.name == DEMO_PROVIDER
    assert state.api_key == ""
    assert state.api_base == ""


async def test_demo_chat_is_deterministic_zero_cost_and_never_calls_litellm_or_socket(
    no_network,
):
    client = _demo_client()
    messages = [{"role": "user", "content": "please do the demo task"}]
    first = await client.chat(messages)
    second = await client.chat(messages)

    assert first.content == second.content
    assert DEMO_MARK in (first.content or "")
    for response in (first, second):
        assert response.usage.prompt_tokens == 0
        assert response.usage.completion_tokens == 0
        assert response.usage.total_tokens == 0
        assert response.usage.cost_cny == 0.0
        assert response.usage.actual_model == DEMO_MODEL
        assert response.usage.upstream_provider == DEMO_PROVIDER


async def test_demo_tool_response_is_stable_and_shaped_for_agent(no_network):
    client = _demo_client()
    tools = [
        {
            "type": "function",
            "function": {"name": "write_file", "parameters": {"type": "object"}},
        }
    ]
    messages = [{"role": "user", "content": "produce the demo artifact"}]

    first = await client.chat(messages, tools=tools)
    second = await client.chat(messages, tools=tools)
    assert first.tool_calls and second.tool_calls
    call = first.tool_calls[0]
    assert set(call) == {"id", "name", "args"}
    assert call["name"] == "write_file"
    assert call == second.tool_calls[0]
    args = json.loads(call["args"])
    assert args["path"] == "DEMO.md"
    assert DEMO_MARK in args["content"]

    after_tool = messages + [
        {"role": "assistant", "content": None, "tool_calls": []},
        {"role": "tool", "tool_call_id": call["id"], "content": "ok"},
    ]
    final = await client.chat(after_tool, tools=tools)
    assert final.tool_calls is None
    assert DEMO_MARK in (final.content or "")


async def test_demo_stream_matches_chat_and_cancellation_propagates(no_network):
    client = _demo_client()
    messages = [{"role": "user", "content": "stream the demo"}]

    chat_response = await client.chat(messages)
    chunks = [c async for c in client.chat_stream(messages)]
    deltas = "".join(c.content for c in chunks[:-1] if c.content)
    final = chunks[-1]
    assert deltas == chat_response.content
    # live 契约: 最终 chunk 携带全量累计内容
    assert final.content == chat_response.content
    final_usage = final.usage
    assert final.finish_reason == "stop"
    assert final_usage is not None and final_usage.total_tokens == 0
    assert final_usage.actual_model == DEMO_MODEL

    async def _consume_partially():
        async for _ in client.chat_stream(messages):
            await asyncio.sleep(3600)

    task = asyncio.ensure_future(_consume_partially())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_demo_planner_and_audit_payloads_parse_with_production_models(no_network):
    client = _demo_client()

    planner_messages = [
        {"role": "system", "content": "# 规划职责\n……"},
        {"role": "user", "content": "敕令旨意：演示"},
    ]
    plan_response = await client.chat(planner_messages)
    plan_data = Planner._extract_json(plan_response.content or "")
    assert plan_data is not None
    tasks = [PlanTask(**task) for task in plan_data["tasks"]]
    assert tasks[0].task_id == "main"
    assert DEMO_MARK in tasks[0].description

    audit_messages = [
        {"role": "system", "content": "You are a quality reviewer. …"},
        {"role": "user", "content": "assess"},
    ]
    audit_response = await client.chat(audit_messages)
    audit_data = json.loads(audit_response.content or "")
    result = AuditResult(verdict=audit_data["verdict"], rules_checked=0)
    assert result.verdict == "pass"


async def test_demo_rubric_payload_is_strict_deterministic_and_zero_network(no_network):
    client = _demo_client()
    prompt = (
        "Rubric:\nThe deterministic gate must pass.\n\n"
        "Output to evaluate:\n[DEMO] candidate output\n\n"
        'Reply with JSON only (no extra text): {"score": <0.0-1.0 float>, '
        '"reasoning": "<short string>"}'
    )

    response = await client.chat([{"role": "user", "content": prompt}])
    payload = json.loads(response.content or "")

    assert payload["score"] == 1.0
    assert DEMO_MARK in payload["reasoning"]
    assert response.usage.total_tokens == 0
    assert response.usage.cost_cny == 0.0


async def test_demo_completion_audit_payload_passes_with_no_gaps_and_zero_network(no_network):
    client = _demo_client()
    prompt = (
        "现在进入完成审计 (completion audit)。\n\n"
        "仅输出一段 JSON（不要其他文字、不要 markdown 包裹）：\n"
        '{"passed": false, "gaps": []}'
    )

    response = await client.chat([{"role": "system", "content": prompt}])
    result = parse_audit_response(response.content or "")

    assert result.passed is True
    assert result.gaps == ()
    assert response.usage.total_tokens == 0
    assert response.usage.cost_cny == 0.0


async def test_live_provider_error_never_falls_through_to_demo(monkeypatch):
    import litellm

    import tianshu.providers.demo as demo_module

    async def _live_failure(**kwargs):
        raise RuntimeError("live provider unavailable")

    monkeypatch.setattr(litellm, "acompletion", _live_failure)

    demo_calls: list[object] = []
    original_demo_chat = demo_module.demo_chat

    async def _spy(messages, tools=None):  # pragma: no cover - 触发即失败
        demo_calls.append(messages)
        return await original_demo_chat(messages, tools)

    monkeypatch.setattr(demo_module, "demo_chat", _spy)

    live_client = LLMClient(model="gpt-4o-mini", api_key="k", max_retries=1)
    with pytest.raises(RuntimeError, match="live provider unavailable"):
        await live_client.chat([{"role": "user", "content": "hello"}])
    assert demo_calls == []


def test_demo_runtime_override_masks_resolution_but_never_persists(tmp_path):
    from tianshu.storage import Storage

    storage = Storage(str(tmp_path / "db.sqlite3"))
    storage.init_db()
    live = LLMConfigState(name="live-main", model="gpt-4o-mini", api_key="k", api_base="")
    baseline = ConfigManager(live, storage=storage)
    assert baseline.state.name == "live-main"
    rows_before = [
        (row["name"], row["model"], bool(row["is_active"])) for row in storage.list_llm_configs()
    ]

    demo_manager = ConfigManager(live, storage=storage, runtime_override=demo_llm_config_state())
    assert demo_manager.state.model == DEMO_MODEL
    # persona/named 覆盖不得逃逸回 live
    assert demo_manager.get_config("live-main").model == DEMO_MODEL
    # 掩蔽不伪造存在性: 不存在的名字仍为 None
    assert demo_manager.get_config("anything") is None
    # 持久层完全不动：无 demo 行，live 行原样
    rows_after = [
        (row["name"], row["model"], bool(row["is_active"])) for row in storage.list_llm_configs()
    ]
    assert rows_after == rows_before
    assert all(name != DEMO_PROVIDER for name, _, _ in rows_after)

    # 下一次 live 启动看到原 active 配置
    relive = ConfigManager(live, storage=storage)
    assert relive.state.name == "live-main"
    assert relive.state.model == "gpt-4o-mini"
    storage.close()


async def test_demo_stream_final_chunk_carries_full_accumulated_content(no_network):
    """live 契约(llm.py chat_stream)最终 chunk 携带全量累计内容; 流式消费方把
    最后一个 chunk 当权威响应, demo 必须形状对齐, 否则 [DEMO] 证据在流式路径丢失。"""
    client = _demo_client()
    chunks = [c async for c in client.chat_stream([{"role": "user", "content": "hello"}])]
    deltas = [c.content for c in chunks[:-1] if c.content]
    final = chunks[-1]
    assert final.finish_reason == "stop"
    assert final.content == "".join(deltas)
    assert final.content and DEMO_MARK in final.content


def test_demo_shape_markers_bind_to_production_prompts():
    """守卫: demo 形状识别标记必须存在于生产 prompt 源文本中; 生产文案漂移时
    本测试红, 提示同步 demo 标记, 防止静默错配。"""
    import tianshu.auditor.reviewer as reviewer_mod
    import tianshu.executor.orchestrator.checks as checks_mod
    import tianshu.executor.orchestrator.templates as templates_mod
    import tianshu.planner.prompts as planner_prompts
    from tianshu.providers import demo

    planner_src = Path(planner_prompts.__file__).read_text(encoding="utf-8")
    for marker in demo._PLANNER_MARKERS:
        assert marker in planner_src, f"planner prompt 已不含 demo 标记 {marker!r}"
    reviewer_src = Path(reviewer_mod.__file__).read_text(encoding="utf-8")
    assert demo._AUDIT_MARKER in reviewer_src, "auditor prompt 已不含 demo audit 标记"
    checks_src = Path(checks_mod.__file__).read_text(encoding="utf-8")
    assert demo._RUBRIC_MARKER in checks_src, "rubric prompt 已不含 demo rubric 标记"
    completion_audit_template = (
        Path(templates_mod.__file__).parent.parent / "templates/edict/completion_audit.md"
    ).read_text(encoding="utf-8")
    assert demo._COMPLETION_AUDIT_MARKER in completion_audit_template, (
        "completion audit prompt 已不含 demo completion audit 标记"
    )


def test_demo_get_config_preserves_existence_semantics(tmp_path):
    """掩蔽不得伪造存在性: demo 下 get_config(不存在的名字) 必须仍为 None,
    否则 persona llm_config_name 存在性校验被静默禁用。"""
    from tianshu.storage import Storage

    storage = Storage(str(tmp_path / "s.sqlite3"))
    storage.init_db()
    live = LLMConfigState(name="live-main", model="gpt-4o-mini", api_key="k", api_base="")
    cm = ConfigManager(live, storage=storage, runtime_override=demo_llm_config_state())
    assert cm.get_config("no-such-config") is None
    masked = cm.get_config("live-main")
    assert masked is not None and masked.model == DEMO_MODEL
    storage.close()
