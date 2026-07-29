"""跟进批示多轮上下文回放（executor/conversation.py）。"""

from __future__ import annotations

from tianshu.executor.conversation import build_conversation_history
from tianshu.models import Edict, Memorial, TaskStatus


def _memorial(edict_id: str, **kwargs) -> Memorial:
    return Memorial(edict_id=edict_id, **kwargs)


def test_replays_prior_turns_as_plain_text():
    """assistant 只回放纯文本，不带 reasoning_content（严格端点会拒收未知字段）。"""
    edict = Edict(goal="hello?")
    first = _memorial(
        edict.id,
        instruction="hello?",
        status=TaskStatus.COMPLETED,
        result="Hi! 我是司马光。",
        reasoning_content="thinking...",
    )
    current = _memorial(edict.id, instruction="前面我问了什么问题?", status=TaskStatus.RUNNING)
    history = build_conversation_history(edict, [first, current], exclude_memorial_id=current.id)
    assert history == [
        {"role": "user", "content": "hello?"},
        {"role": "assistant", "content": "Hi! 我是司马光。"},
    ]


def test_only_completed_memorials_are_replayed():
    """FAILED/CANCELLED 不回放：避免悬空 user 指令与重试链同指令重复投喂。"""
    edict = Edict(goal="g")
    running = _memorial(edict.id, instruction="进行中", status=TaskStatus.RUNNING)
    failed = _memorial(edict.id, instruction="失败的", status=TaskStatus.FAILED, result="部分产出")
    cancelled = _memorial(edict.id, instruction="取消的", status=TaskStatus.CANCELLED)
    done = _memorial(edict.id, instruction="成功的", status=TaskStatus.COMPLETED, result="ok")
    history = build_conversation_history(edict, [running, failed, cancelled, done])
    assert history == [
        {"role": "user", "content": "成功的"},
        {"role": "assistant", "content": "ok"},
    ]


def test_dag_node_memorials_are_skipped():
    """DAG 子节点奏折是机器生成的任务分解，不能伪装成用户对话轮。"""
    edict = Edict(goal="拆解任务")
    node = _memorial(
        edict.id,
        instruction="子任务：检索资料",
        status=TaskStatus.COMPLETED,
        result="节点产出",
        dag_node_id="n1",
    )
    root = _memorial(edict.id, instruction="拆解任务", status=TaskStatus.COMPLETED, result="汇总")
    history = build_conversation_history(edict, [node, root])
    assert history == [
        {"role": "user", "content": "拆解任务"},
        {"role": "assistant", "content": "汇总"},
    ]


def test_turn_budget_keeps_most_recent():
    edict = Edict(goal="g")
    memorials = [
        _memorial(edict.id, instruction=f"q{i}", status=TaskStatus.COMPLETED, result=f"a{i}")
        for i in range(30)
    ]
    history = build_conversation_history(edict, memorials, max_turns=3)
    assert len(history) == 6
    assert history[0]["content"] == "q27"
    assert history[-1]["content"] == "a29"


def test_char_budget_drops_oldest_but_keeps_latest_turn():
    edict = Edict(goal="g")
    big = "x" * 500
    memorials = [
        _memorial(edict.id, instruction=f"q{i}", status=TaskStatus.COMPLETED, result=big)
        for i in range(5)
    ]
    history = build_conversation_history(edict, memorials, max_chars=1200)
    # 每轮 ~502 字符：预算 1200 只装得下最近 2 轮
    assert [m["content"] for m in history if m["role"] == "user"] == ["q3", "q4"]
    # 单轮超预算也至少保留最近一轮
    history_tiny = build_conversation_history(edict, memorials, max_chars=10)
    assert [m["content"] for m in history_tiny if m["role"] == "user"] == ["q4"]


def test_instruction_falls_back_to_goal():
    edict = Edict(goal="总目标")
    done = _memorial(edict.id, status=TaskStatus.COMPLETED, result="ok")
    done.instruction = None
    assert build_conversation_history(edict, [done])[0] == {
        "role": "user",
        "content": "总目标",
    }
