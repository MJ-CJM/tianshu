"""Diagnostician:失败症状采集、已试假设去重、allowlist 过滤、失败安全。"""

import json

from tianshu.models.common import AuditResult
from tianshu.universe.diagnostician import Diagnostician


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, payload):
        self._payload = payload
        self.prompts = []

    async def chat(self, messages):
        self.prompts.append(messages[-1]["content"])
        return _FakeResp(self._payload)


class _FakeEdict:
    def __init__(self, goal):
        self.goal = goal


class _FakeMemorial:
    def __init__(self, edict_id, error=None, audit_json=None, audit=None):
        self.edict_id = edict_id
        self.error = error
        self.audit_json = audit_json
        self.audit = audit


class _FakeStorage:
    def __init__(self, memorials=(), universes=()):
        self._mems = list(memorials)
        self._unis = list(universes)

    def list_memorials(self, status=None, limit=50, offset=0):
        rows = self._mems[:limit]
        return (rows, len(self._mems))

    def get_edict(self, edict_id):
        return _FakeEdict(goal=f"目标-{edict_id}")

    def list_universes(self, include_archived=True):
        return self._unis


def _diag(payload, memorials=(), universes=()):
    return Diagnostician(
        _FakeLLM(payload),
        _FakeStorage(memorials, universes),
        evolvable_paths=("src/tianshu/planner/", "src/tianshu/tools/http.py"),
    )


async def test_diagnose_returns_allowlisted_hypotheses():
    payload = json.dumps(
        [
            {
                "target_path": "src/tianshu/planner/planner.py",
                "hypothesis": "拆解超长目标时先分段",
                "rationale": "3 条超时失败",
            },
            {
                "target_path": "src/tianshu/executor/agent.py",  # 演化域外 → 过滤
                "hypothesis": "越界提案",
                "rationale": "x",
            },
            {
                "target_path": "src/tianshu/planner/",  # 目录不是可改写文件
                "hypothesis": "目录提案",
                "rationale": "x",
            },
        ]
    )
    mems = [_FakeMemorial("e1", error="timeout", audit_json={"reasons": ["拆解过粗"]})]
    result = await _diag(payload, mems).diagnose(max_hypotheses=3)
    assert len(result) == 1
    assert result[0]["target_path"] == "src/tianshu/planner/planner.py"


async def test_diagnose_no_failures_returns_empty():
    result = await _diag("[]").diagnose()
    assert result == []


async def test_diagnose_prompt_carries_symptoms_and_tried():
    payload = "[]"
    mems = [_FakeMemorial("e1", error="TimeoutError: 300s")]
    unis = [
        {
            "origin": "code_variant",
            "description": "已试:planner 分段",
            "created_at": "2026-07-01T00:00:00+00:00",
        }
    ]
    diag = _diag(payload, mems, unis)
    await diag.diagnose()
    prompt = diag._llm.prompts[0]
    assert "TimeoutError" in prompt
    assert "已试:planner 分段" in prompt


async def test_diagnose_bad_llm_output_fails_safe():
    mems = [_FakeMemorial("e1", error="boom")]
    result = await _diag("这不是 JSON", mems).diagnose()
    assert result == []


async def test_diagnose_collects_str_audit_json():
    """audit_json 若是未反序列化的 JSON 字符串(而非 dict),也要防御解析出审计意见。"""
    payload = "[]"
    mems = [_FakeMemorial("e1", error="timeout", audit_json=json.dumps({"reasons": ["拆解过粗"]}))]
    diag = _diag(payload, mems)
    await diag.diagnose()
    prompt = diag._llm.prompts[0]
    assert "拆解过粗" in prompt


async def test_diagnose_collects_audit_reasons():
    """memorial.audit.reasons 属性的审计意见应被发给 LLM——测试 .audit 属性主路径。"""
    payload = "[]"
    audit = AuditResult(reasons=["拆解过粗", "缺验证"])
    mems = [_FakeMemorial("e1", error="timeout", audit=audit)]
    diag = _diag(payload, mems)
    await diag.diagnose()
    prompt = diag._llm.prompts[0]
    assert "拆解过粗" in prompt
    assert "缺验证" in prompt
