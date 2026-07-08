"""太医奏折(compile_memorial 纯函数 / Diagnostician.report):汇编正确 + 失败安全。"""

from tianshu.universe.diagnostician import Diagnostician, compile_memorial


def _diagnosis(target: str, hypothesis: str, rationale: str = "x") -> dict:
    return {"target_path": target, "hypothesis": hypothesis, "rationale": rationale}


def test_compile_memorial_with_findings():
    diagnoses = [
        _diagnosis("src/tianshu/planner/planner.py", "拆解超长目标时先分段"),
        _diagnosis("src/tianshu/tools/http.py", "重试加指数退避"),
    ]
    memorial = compile_memorial(diagnoses)
    assert memorial["type"] == "taiyi.memorial"
    assert memorial["title"] == "太医奏折"
    assert memorial["count"] == 2
    assert memorial["findings"] == [
        {"target": "src/tianshu/planner/planner.py", "hypothesis": "拆解超长目标时先分段"},
        {"target": "src/tianshu/tools/http.py", "hypothesis": "重试加指数退避"},
    ]
    assert "2" in memorial["summary"]
    assert "未见沉疴" not in memorial["summary"]


def test_compile_memorial_empty_is_healthy():
    memorial = compile_memorial([])
    assert memorial["type"] == "taiyi.memorial"
    assert memorial["title"] == "太医奏折"
    assert memorial["count"] == 0
    assert memorial["findings"] == []
    assert "未见沉疴" in memorial["summary"]


class _StubDiagnostician(Diagnostician):
    """report 只依赖 self.diagnose——桩掉 diagnose,不触真 LLM/storage。"""

    def __init__(self, *, result: list[dict] | None = None, boom: bool = False) -> None:
        super().__init__(None, None, evolvable_paths=())
        self._result = result if result is not None else []
        self._boom = boom
        self.seen_max: int | None = None

    async def diagnose(self, *, max_hypotheses: int = 3) -> list[dict]:
        self.seen_max = max_hypotheses
        if self._boom:
            raise RuntimeError("诊断炸了")
        return self._result


async def test_report_compiles_memorial_from_diagnose():
    diag = _StubDiagnostician(result=[_diagnosis("src/tianshu/planner/planner.py", "先分段")])
    memorial = await diag.report()
    assert diag.seen_max == 5  # report 默认 max_hypotheses=5 已转发给 diagnose
    assert memorial["type"] == "taiyi.memorial"
    assert memorial["count"] == 1
    assert memorial["findings"] == [
        {"target": "src/tianshu/planner/planner.py", "hypothesis": "先分段"}
    ]


async def test_report_fails_safe_when_diagnose_raises():
    diag = _StubDiagnostician(boom=True)
    memorial = await diag.report()  # 不抛异常
    assert memorial["count"] == 0
    assert memorial["findings"] == []
    assert "未见沉疴" in memorial["summary"]
