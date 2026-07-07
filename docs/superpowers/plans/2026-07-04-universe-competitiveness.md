# 位面(Universe)竞争力优化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复位面演化闭环的三处有效性硬伤(探索路由失真、跨分布比较、评估污染),补齐演化记忆与自主假设生成,加固评估统计与晋升信任面——让"平台自我进化"从骨架变成真闭环。

**Architecture:** 晋升判定统一收敛到"同评估集配对沙箱评估"(variant 与 champion 基线在同一 eval_set、同一沙箱环境下各跑一次,margin 判在配对差上);在线信号只归 champion(运行观测),行为层 challenger 的 fitness 一律来自沙箱配对评估;演化引擎补"记忆"(历史尝试进 prompt)与"诊断"(失败奏折/审计意见 → 假设 → 配额内自主提案)。

**Tech Stack:** Python 3.12 / FastAPI / SQLite / pydantic-settings(env 前缀 `TIANSHU_`)/ React 18 + antd 5。

**背景分析:** 见本轮会话的竞争力分析(对标 AlphaEvolve / Darwin Gödel Machine / ShinkaEvolve);设计文档 `docs/design/universe/{evolution,eval,code-variant}.md`;实现现状 `docs/impl/universe/README.md`。

## Global Constraints

- 测试命令一律用 `.venv/bin/python -m pytest`(裸 python 缺依赖);每任务收尾 `.venv/bin/ruff check src tests` 与 `.venv/bin/ruff format --check src tests` 双净。
- 测试基线 **1364 passed** 只增不减(`-m "not slow"`)。
- commit 不带任何 attribution 尾注;消息格式 `<type>: <描述>`(中文)。
- 中文注释/文档保留古风隐喻(位面/冠军/候选/太医等)。
- API 路由快照:仅允许新增 `POST /api/universes/propose-auto`(T8);其余 URL 零漂移(可用 `scripts/dump_routes.py` 对照)。
- 失败安全原则沿用:演化/评估路径任何异常不得影响平台主流程(catch → 记录 → 降级)。
- 配置删除仅限计划言明的两项:`universe_explore_ratio`、`universe_challenger_fail_limit`(前端/API 无引用,已核实)。

## 明确不做(本轮 out of scope)

- per-run 位面装配(在线探索路由的"真修复")——留待将来,本轮探索路由退役;
- MAP-Elites / island 模型 / embedding novelty 去重(样本量撑不起);
- cassette 录放评估(价值高但工程量大,单独排期);
- 并行沙箱评估(每日 1-3 个提案串行足够);
- 重复评估取均值/置信区间(单次同集配对已消除主要偏差;待运行期观察噪声再议);
- UniversePage 页内文案 i18n 化(现状即硬编码中文,保持一致,i18n 债记文档)。

---

# 批次 1:修真——P0 有效性修复

## Task 1: 探索路由退役 + challenger fitness 写入防线

行为层 challenger 的配置只有晋升(switch)后才加载到 live,在线"探索"分给它的流量实际仍以冠军配置执行——fitness 归因失真,margin 比较是噪声对噪声。本任务退役探索路由,并保证在线 fitness 信号只归 champion。

**Files:**
- Modify: `src/tianshu/universe/manager.py:55-74`(route_for_memorial)
- Modify: `src/tianshu/config_manager.py:50,55`(删 `universe_explore_ratio`、`universe_challenger_fail_limit`)
- Modify: `src/tianshu/bootstrap/universe_hooks.py`(_update_universe_fitness 加 champion 判断)
- Modify: `src/tianshu/universe/evolver.py:157-168`(删 `_retire_failing_challengers`,run() 中对应调用一并删;劣质候选下线改由 Task 5 的评估分流承担)
- Test: `tests/universe/test_routing.py`(重写)、`tests/universe/test_universe_hooks.py`(新建)
- Test: `tests/universe/test_evolver.py`(删除/改写 retire 相关用例)

**Interfaces:**
- Produces: `route_for_memorial(memorial_id) -> str | None` 语义变为"一律返回 champion_id"(签名不变,executor.py:203 调用点零改动)。
- Produces: `_update_universe_fitness` 仅当 memorial 归属位面 == champion 时才重算写入。

- [ ] **Step 1: 重写 test_routing.py 为退役语义**

```python
"""探索路由已退役:无论开关/候选状态,route_for_memorial 一律归冠军。"""


class _FakeStorage:
    def __init__(self, champion=None, universes=()):
        self._champ = champion
        self._unis = list(universes)

    def get_champion_universe(self):
        return self._champ

    def list_universes(self, include_archived=True):
        return [u for u in self._unis if include_archived or u["status"] != "archived"]


def _mgr(champion, universes=()):
    from tianshu.universe.manager import UniverseManager

    return UniverseManager(
        storage=_FakeStorage(champion, universes),
        store=None,
        persona_loader=None,
        skills_loader=None,
        config_snapshot=lambda: {},
        config_apply=lambda m: None,
    )


def test_route_returns_champion_even_with_challengers():
    champ = {"id": "u-champ", "status": "champion"}
    challenger = {"id": "u-chal", "status": "challenger", "code_ref": None}
    mgr = _mgr(champ, [champ, challenger])
    for i in range(20):  # 任意 memorial_id 都归冠军(旧版曾按哈希分桶)
        assert mgr.route_for_memorial(f"mem-{i}") == "u-champ"


def test_route_returns_none_without_champion():
    assert _mgr(None).route_for_memorial("mem-1") is None
```

- [ ] **Step 2: 跑测试确认红**

Run: `.venv/bin/python -m pytest tests/universe/test_routing.py -q`
Expected: FAIL(旧实现按 explore_ratio 分桶,部分 memorial 会命中 challenger)

- [ ] **Step 3: 简化 route_for_memorial**

替换 `manager.py` 的 route_for_memorial 整个方法(原 55-74 行):

```python
    def route_for_memorial(self, memorial_id: str) -> str | None:
        """返回本 memorial 应归属的位面——当前一律归冠军(仅作归因标记)。

        历史版本曾按 explore_ratio 把流量哈希分桶给 challenger,但 challenger
        的行为配置只有晋升(switch)后才会加载到 live,被"探索"到的流量实际仍以
        冠军配置执行,fitness 归因失真。探索路由退役;challenger 的适应度改由
        沙箱配对评估产生(evolver),待支持 per-run 位面装配后再恢复在线探索。
        """
        return self.champion_id()
```

同时删除文件顶部 `import hashlib`(若无其他使用)。

- [ ] **Step 4: 删两个死配置**

`config_manager.py` 删除两行:

```python
    universe_explore_ratio: float = 0.1          # ← 删除
    universe_challenger_fail_limit: int = 5      # ← 删除
```

- [ ] **Step 5: universe_hooks 加 champion 防线 + 新建其测试**

`bootstrap/universe_hooks.py` 的 `_update_universe_fitness`,在拿到 `universe_id` 后加判断:

```python
    if not universe_id:
        return
    champ = storage.get_champion_universe()
    if not champ or champ["id"] != universe_id:
        # challenger 的 fitness 由沙箱配对评估负责(evolver 写入),
        # 在线运行信号只累积给冠军,避免覆盖沙箱评估分。
        return
    stats = storage.universe_memorial_stats(universe_id)
```

新建 `tests/universe/test_universe_hooks.py`:

```python
"""_update_universe_fitness:在线信号只归 champion,不覆盖 challenger 的沙箱评估分。"""

from tianshu.bootstrap.universe_hooks import _update_universe_fitness


class _FakeMemorial:
    def __init__(self, universe_id):
        self.universe_id = universe_id


class _FakeStorage:
    def __init__(self, champion_id):
        self._champ = {"id": champion_id} if champion_id else None
        self.updated = []

    def get_memorial(self, memorial_id):
        return _FakeMemorial("u-target")

    def get_champion_universe(self):
        return self._champ

    def universe_memorial_stats(self, universe_id):
        return {"total": 5, "success": 5, "retries": 0, "audited": 0,
                "audit_pass": 0, "cost": 0.0, "feedback": 0}

    def update_universe_fitness(self, universe_id, fitness):
        self.updated.append(universe_id)


class _FakeConfigManager:
    class agent_config:  # noqa: N801
        parallel_universe_enabled = True
        universe_fitness_weights = (0.4, 0.15, 0.2, 0.1, 0.15)


class _FakeEvent:
    memorial_id = "mem-1"


async def test_champion_fitness_updated():
    storage = _FakeStorage(champion_id="u-target")
    await _update_universe_fitness(
        _FakeEvent(), config_manager=_FakeConfigManager(), storage=storage)
    assert storage.updated == ["u-target"]


async def test_challenger_fitness_not_overwritten():
    storage = _FakeStorage(champion_id="u-other")  # memorial 归属 u-target ≠ 冠军
    await _update_universe_fitness(
        _FakeEvent(), config_manager=_FakeConfigManager(), storage=storage)
    assert storage.updated == []
```

(项目 pytest 为 asyncio_mode=auto,async 测试无需装饰器。)

- [ ] **Step 6: 删 _retire_failing_challengers**

`evolver.py`:删除 `_retire_failing_challengers` 方法(157-168 行)及 `run()` 中的调用行 `result.retired = self._retire_failing_challengers(cfg)`(改为保留 `result.retired` 默认空列表,Task 5 的评估分流会往里追加)。同步删除 `tests/universe/test_evolver.py` 中 retire 相关用例(以 `retire` 关键字定位)。

- [ ] **Step 7: 全量验证 + 提交**

Run: `.venv/bin/python -m pytest tests/universe -q && .venv/bin/python -m pytest -q -m "not slow" && .venv/bin/ruff check src tests`
Expected: 全绿(本任务删除旧探索/retire 用例、新增 4 条;净变化以实际为准,报告中列明删除清单)

```bash
git add -A
git commit -m "fix(universe): 探索路由退役——challenger 不真正运行,在线 fitness 只归冠军"
```

## Task 2: 沙箱 extra_env 通用注入机制

行为层沙箱评估(Task 5)需要向沙箱进程注入 `TIANSHU_RUNTIME_PERSONAS_DIR` 覆盖 personas 目录;eval 凭证隔离(Task 10)需要注入低额度 LLM key。本任务提供统一的 env 注入口。

**Files:**
- Modify: `src/tianshu/universe/sandbox.py`(_build_env / start / session)
- Modify: `src/tianshu/universe/eval_harness.py`(evaluate 透传)
- Test: `tests/universe/test_sandbox.py`

**Interfaces:**
- Produces: `SandboxRunner.session(worktree, *, db_path, extra_env: dict[str, str] | None = None)`;`start` 同参;`_build_env(worktree, db_path, port, extra_env=None)`。
- Produces: `EvalHarness.evaluate(..., extra_env: dict[str, str] | None = None)`,原有调用零改动(默认 None)。

- [ ] **Step 1: 写失败测试(tests/universe/test_sandbox.py 追加)**

```python
def test_build_env_extra_env_overrides():
    from tianshu.universe.sandbox import SandboxRunner

    runner = SandboxRunner()
    env = runner._build_env(
        Path("/tmp/wt"), Path("/tmp/db.sqlite"), 12345,
        extra_env={"TIANSHU_RUNTIME_PERSONAS_DIR": "/tmp/personas", "TIANSHU_LLM_API_KEY": "sk-eval"},
    )
    assert env["TIANSHU_RUNTIME_PERSONAS_DIR"] == "/tmp/personas"
    assert env["TIANSHU_LLM_API_KEY"] == "sk-eval"
    assert env["TIANSHU_EVAL_MODE"] == "1"          # 原有注入不受影响
    assert env["TIANSHU_DB_PATH"] == "/tmp/db.sqlite"


def test_build_env_extra_env_cannot_unset_eval_mode():
    from tianshu.universe.sandbox import SandboxRunner

    env = SandboxRunner()._build_env(
        Path("/tmp/wt"), Path("/tmp/db.sqlite"), 12345,
        extra_env={"TIANSHU_EVAL_MODE": "0"},
    )
    assert env["TIANSHU_EVAL_MODE"] == "1"  # 安全围栏字段不可被覆盖
```

(文件已 `from pathlib import Path` 则不重复导入;无则补。)

- [ ] **Step 2: 跑测试确认红**

Run: `.venv/bin/python -m pytest tests/universe/test_sandbox.py -q`
Expected: FAIL — `_build_env() got an unexpected keyword argument 'extra_env'`

- [ ] **Step 3: 实现**

`sandbox.py` 三处修改:

```python
    def _build_env(
        self,
        worktree: Path,
        db_path: Path,
        port: int,
        extra_env: dict[str, str] | None = None,
    ) -> dict:
        env = dict(os.environ)
        src = str(Path(worktree) / "src")
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else src
        if extra_env:
            env.update({k: str(v) for k, v in extra_env.items()})
        # 安全围栏字段最后写入,extra_env 不可覆盖(评估进程必须始终处于隔离态)
        env["TIANSHU_DB_PATH"] = str(db_path)
        env["TIANSHU_PORT"] = str(port)
        env["TIANSHU_HOST"] = self._host
        env["TIANSHU_EVAL_MODE"] = "1"
        return env

    def start(
        self, worktree: Path, *, db_path: Path, extra_env: dict[str, str] | None = None
    ) -> SandboxHandle:
        ...
        env = self._build_env(wt, db_path, port, extra_env=extra_env)
        ...

    @contextlib.contextmanager
    def session(
        self, worktree: Path, *, db_path: Path, extra_env: dict[str, str] | None = None
    ):
        handle = self.start(worktree, db_path=db_path, extra_env=extra_env)
        try:
            yield handle
        finally:
            self.stop(handle)
```

`eval_harness.py` 的 `evaluate` 签名加 `extra_env: dict | None = None`,`session(...)` 调用改为 `self._sandbox.session(worktree, db_path=iso_db, extra_env=extra_env)`。

- [ ] **Step 4: 验证 + 提交**

Run: `.venv/bin/python -m pytest tests/universe/test_sandbox.py tests/universe/test_eval_harness.py -q && .venv/bin/ruff check src tests`
Expected: PASS

```bash
git add -A
git commit -m "feat(universe): 沙箱支持 extra_env 注入(围栏字段不可覆盖),为行为层评估与凭证隔离铺路"
```

## Task 3: 评估预算闸

沙箱回放烧真实 LLM 费用且此前无上限;行为层评估(Task 5)上线后 cron 每日自动评估,必须先有预算闸。

**Files:**
- Modify: `src/tianshu/universe/eval_harness.py`(evaluate 加 budget_cny)
- Modify: `src/tianshu/config_manager.py`(AgentConfig 加 `code_variant_eval_budget_cny: float = 20.0`,加在 `code_variant_eval_set_size` 之后)
- Modify: `src/tianshu/universe/evolver.py:302-304`(propose_code_variant 传预算)
- Test: `tests/universe/test_eval_harness.py`

**Interfaces:**
- Produces: `EvalHarness.evaluate(..., budget_cny: float | None = None) -> dict`,返回 dict 新增键 `"truncated": bool`(预算触顶提前截断时 True),原有键不变。

- [ ] **Step 1: 写失败测试(参考 test_eval_harness.py 既有 fake sandbox 风格,追加)**

```python
def test_evaluate_truncates_on_budget(tmp_path, monkeypatch):
    """预算触顶后停止回放剩余 goal,结果标记 truncated。"""
    from tianshu.universe.eval_harness import EvalHarness

    ran: list[str] = []

    class _H:
        base_url = "http://x"
        db_path = tmp_path / "_eval.db"

    class _FakeSandbox:
        import contextlib

        @contextlib.contextmanager
        def session(self, worktree, *, db_path, extra_env=None):
            yield _H()

    harness = EvalHarness(storage=None, sandbox_runner=_FakeSandbox())
    # 每回放一条 goal,沙箱 DB 里累积 1.0 元成本
    monkeypatch.setattr(harness, "_run_goal", lambda base, goal, t: ran.append(goal))
    costs = iter([1.0, 2.0, 3.0, 3.0])  # 第 2 条后 cost=2.0 ≥ budget → 截断
    monkeypatch.setattr(
        harness, "aggregate_db_stats",
        lambda db: {"total": len(ran), "success": len(ran), "retries": 0,
                    "audited": 0, "audit_pass": 0, "cost": next(costs), "feedback": 0})

    result = harness.evaluate(tmp_path, eval_set=["g1", "g2", "g3"], budget_cny=2.0)
    assert result["truncated"] is True
    assert ran == ["g1", "g2"]
    assert result["n"] == 2


def test_evaluate_no_budget_runs_all(tmp_path, monkeypatch):
    """不传预算时全量回放,truncated 恒 False。"""
    from tianshu.universe.eval_harness import EvalHarness

    ran: list[str] = []

    class _H:
        base_url = "http://x"
        db_path = tmp_path / "_eval.db"

    class _FakeSandbox:
        import contextlib

        @contextlib.contextmanager
        def session(self, worktree, *, db_path, extra_env=None):
            yield _H()

    harness = EvalHarness(storage=None, sandbox_runner=_FakeSandbox())
    monkeypatch.setattr(harness, "_run_goal", lambda base, goal, t: ran.append(goal))
    costs = iter([1.0, 2.0, 3.0, 3.0])
    monkeypatch.setattr(
        harness, "aggregate_db_stats",
        lambda db: {"total": len(ran), "success": len(ran), "retries": 0,
                    "audited": 0, "audit_pass": 0, "cost": next(costs), "feedback": 0})

    result = harness.evaluate(tmp_path, eval_set=["g1", "g2", "g3"])
    assert result["truncated"] is False
    assert ran == ["g1", "g2", "g3"]
    assert result["n"] == 3
```

- [ ] **Step 2: 跑测试确认红**

Run: `.venv/bin/python -m pytest tests/universe/test_eval_harness.py -q -k budget`
Expected: FAIL — `evaluate() got an unexpected keyword argument 'budget_cny'`

- [ ] **Step 3: 实现 evaluate 预算闸**

```python
    def evaluate(
        self,
        worktree: Path,
        *,
        eval_set: list[str],
        seed_db: Path | None = None,
        goal_timeout_s: int = 300,
        extra_env: dict | None = None,
        budget_cny: float | None = None,
    ) -> dict:
        """在沙箱中回放 eval_set,聚合并打分。

        budget_cny 非 None 时逐条回放后检查沙箱 DB 累计成本,触顶即截断
        (truncated=True),已回放部分照常聚合打分——评估必须失败安全,
        预算闸只截断、不作废。
        """
        iso_db = Path(worktree).parent / "_eval.db"
        if seed_db is not None:
            shutil.copy(seed_db, iso_db)
        truncated = False
        ran = 0
        with self._sandbox.session(worktree, db_path=iso_db, extra_env=extra_env) as h:
            for goal in eval_set:
                self._run_goal(h.base_url, goal, goal_timeout_s)
                ran += 1
                if budget_cny is not None and self.aggregate_db_stats(h.db_path)["cost"] >= budget_cny:
                    truncated = True
                    logger.warning(
                        "eval: budget %.2f CNY reached after %d/%d goals, truncating",
                        budget_cny, ran, len(eval_set))
                    break
            stats = self.aggregate_db_stats(h.db_path)
        return {"fitness": self.score(stats), "stats": stats, "n": ran, "truncated": truncated}
```

`config_manager.py` AgentConfig 加(紧随 `code_variant_eval_set_size` 之后):

```python
    code_variant_eval_budget_cny: float = 20.0
```

`evolver.py` propose_code_variant 的 evaluate 调用改为:

```python
            ev = await asyncio.to_thread(
                self._eval_harness.evaluate,
                worktree,
                eval_set=es,
                budget_cny=getattr(cfg, "code_variant_eval_budget_cny", None),
            )
```

并在 save_variant_eval_run 的 fitness 里带上截断标记(供 UI 识别):

```python
            fitness = ev["fitness"]
            if ev.get("truncated"):
                fitness = {**fitness, "truncated": True}
```

- [ ] **Step 4: 验证 + 提交**

Run: `.venv/bin/python -m pytest tests/universe -q && .venv/bin/ruff check src tests`
Expected: PASS

```bash
git add -A
git commit -m "feat(universe): 沙箱评估预算闸——累计成本触顶截断,默认 20 元/次"
```

## Task 4: 代码变体配对基线——margin 判在同集配对差上

现状 `propose_code_variant` 拿变体的沙箱回放分与冠军的**在线累积分**比 margin——评估集、环境、时段都不同,是跨分布比较。本任务让冠军在同一评估集、同一沙箱环境下跑基线,margin 判在配对差上;基线按评估集指纹缓存复用。

**Files:**
- Modify: `src/tianshu/universe/eval_harness.py`(加 `eval_set_fingerprint` / `evaluate_paired`)
- Modify: `src/tianshu/universe/code_store.py`(加 `repo_root` 只读属性)
- Modify: `src/tianshu/storage/migrations.py`(variant_eval_runs 加列 baseline_json)
- Modify: `src/tianshu/storage/universe_repo.py`(save/list/_row_to_eval_run 同步 + `latest_baseline_fitness`)
- Modify: `src/tianshu/universe/evolver.py`(propose_code_variant 改配对判定)
- Test: `tests/universe/test_eval_harness.py`、`tests/universe/test_storage_universe.py`、`tests/universe/test_evolver_code.py`

**Interfaces:**
- Produces: `EvalHarness.eval_set_fingerprint(eval_set: list[str], champion_key: str) -> str`(sha256 前 12 位;champion 晋升换 ID → 指纹变 → 基线自动失效)。
- Produces: `EvalHarness.evaluate_paired(variant_worktree, *, eval_set, baseline_worktree, variant_env=None, baseline_env=None, goal_timeout_s=300, budget_cny=None, cached_baseline=None) -> dict`,返回 `{"variant": <evaluate返回>, "baseline": <evaluate返回或cached>, "delta": float, "baseline_cached": bool}`。
- Produces: `Storage.latest_baseline_fitness(eval_set_version: str) -> dict | None`。
- Produces: `save_variant_eval_run(run)` 接受可选键 `"baseline"`(dict,存入 baseline_json 列);`list_variant_eval_runs` 返回行含 `"baseline"`(dict | None)。

- [ ] **Step 1: 写失败测试**

`tests/universe/test_eval_harness.py` 追加:

```python
def test_eval_set_fingerprint_stable_and_sensitive():
    from tianshu.universe.eval_harness import EvalHarness

    fp1 = EvalHarness.eval_set_fingerprint(["a", "b"], "champ-1")
    assert fp1 == EvalHarness.eval_set_fingerprint(["a", "b"], "champ-1")
    assert len(fp1) == 12
    assert fp1 != EvalHarness.eval_set_fingerprint(["a", "c"], "champ-1")   # 集合变
    assert fp1 != EvalHarness.eval_set_fingerprint(["a", "b"], "champ-2")   # 冠军变


def test_evaluate_paired_delta_and_cache(tmp_path, monkeypatch):
    from tianshu.universe.eval_harness import EvalHarness

    harness = EvalHarness(storage=None, sandbox_runner=None)
    calls: list[str] = []

    def _fake_evaluate(worktree, *, eval_set, extra_env=None, **kw):
        calls.append(str(worktree))
        score = 0.8 if "variant" in str(worktree) else 0.7
        return {"fitness": {"score": score, "samples": len(eval_set)},
                "stats": {"cost": 1.0}, "n": len(eval_set), "truncated": False}

    monkeypatch.setattr(harness, "evaluate", _fake_evaluate)

    r = harness.evaluate_paired(
        tmp_path / "variant", eval_set=["g1"], baseline_worktree=tmp_path / "main")
    assert r["delta"] == 0.1
    assert r["baseline_cached"] is False
    assert len(calls) == 2

    calls.clear()
    r2 = harness.evaluate_paired(
        tmp_path / "variant", eval_set=["g1"], baseline_worktree=tmp_path / "main",
        cached_baseline={"fitness": {"score": 0.75, "samples": 1}, "stats": {}, "n": 1})
    assert r2["baseline_cached"] is True
    assert round(r2["delta"], 4) == 0.05
    assert calls == [str(tmp_path / "variant")]  # 命中缓存只评 variant
```

`tests/universe/test_storage_universe.py` 追加(沿用文件内既有 `:memory:` Storage fixture 写法):

```python
def test_eval_run_baseline_roundtrip_and_latest(storage):
    storage.save_universe(_universe_row("u-1"))  # 沿用文件内既有造数 helper;无则手写最小 row
    storage.save_variant_eval_run({
        "id": "r1", "universe_id": "u-1", "gate_passed": True,
        "fitness": {"score": 0.8}, "baseline": {"score": 0.7},
        "eval_set_version": "fp-abc", "cost": 1.0, "created_at": "2026-07-04T01:00:00+00:00"})
    storage.save_variant_eval_run({
        "id": "r2", "universe_id": "u-1", "gate_passed": True,
        "fitness": {"score": 0.9}, "baseline": {"score": 0.72},
        "eval_set_version": "fp-abc", "cost": 1.0, "created_at": "2026-07-04T02:00:00+00:00"})

    runs = storage.list_variant_eval_runs("u-1")
    assert runs[0]["baseline"] == {"score": 0.72}          # 按 created_at 倒序

    assert storage.latest_baseline_fitness("fp-abc") == {"score": 0.72}
    assert storage.latest_baseline_fitness("fp-nope") is None
```

- [ ] **Step 2: 跑测试确认红**

Run: `.venv/bin/python -m pytest tests/universe/test_eval_harness.py tests/universe/test_storage_universe.py -q -k "fingerprint or paired or baseline"`
Expected: FAIL(方法不存在 / 列不存在)

- [ ] **Step 3: 实现 EvalHarness 两个新方法**

`eval_harness.py`(文件顶部补 `import hashlib`):

```python
    @staticmethod
    def eval_set_fingerprint(eval_set: list[str], champion_key: str) -> str:
        """评估集指纹:内容 + 冠军标识。冠军更替或选集内容变化都会使基线缓存失效。"""
        payload = "\n".join(eval_set) + "|" + champion_key
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

    def evaluate_paired(
        self,
        variant_worktree: Path,
        *,
        eval_set: list[str],
        baseline_worktree: Path,
        variant_env: dict | None = None,
        baseline_env: dict | None = None,
        goal_timeout_s: int = 300,
        budget_cny: float | None = None,
        cached_baseline: dict | None = None,
    ) -> dict:
        """变体与冠军基线在同一评估集上各评一次,margin 判在配对差上。

        cached_baseline(同指纹的历史基线)命中时跳过基线评估,评估成本减半。
        """
        variant = self.evaluate(
            variant_worktree, eval_set=eval_set, extra_env=variant_env,
            goal_timeout_s=goal_timeout_s, budget_cny=budget_cny)
        if cached_baseline is not None:
            baseline = cached_baseline
            baseline_cached = True
        else:
            baseline = self.evaluate(
                baseline_worktree, eval_set=eval_set, extra_env=baseline_env,
                goal_timeout_s=goal_timeout_s, budget_cny=budget_cny)
            baseline_cached = False
        delta = round(
            variant["fitness"].get("score", 0.0) - baseline["fitness"].get("score", 0.0), 4)
        return {"variant": variant, "baseline": baseline,
                "delta": delta, "baseline_cached": baseline_cached}
```

注意 `cached_baseline` 兼容两种形态:`latest_baseline_fitness` 返回的是裸 fitness dict(`{"score": ...}`),测试里传的是完整 evaluate 返回。统一为:`evaluate_paired` 里对 cached_baseline 做归一——

```python
        if cached_baseline is not None:
            baseline = (cached_baseline if "fitness" in cached_baseline
                        else {"fitness": cached_baseline, "stats": {}, "n": 0, "truncated": False})
```

`code_store.py` CodeVariantStore 加属性(`__init__` 已存 `self._repo_root = Path(repo_root).resolve()`,以文件实际私有名为准):

```python
    @property
    def repo_root(self) -> Path:
        return self._repo_root
```

- [ ] **Step 4: 实现存储层**

`storage/migrations.py` 的迁移 SQL 列表末尾追加:

```python
        # 2026-07-04: 位面竞争力——配对基线(冠军同集沙箱评估分)落台账
        "ALTER TABLE variant_eval_runs ADD COLUMN baseline_json TEXT",
```

`storage/schema.py:239` 的 CREATE TABLE variant_eval_runs 同步加列 `baseline_json TEXT`(新库直建含列;旧库靠迁移)。

`storage/universe_repo.py`:

```python
    def save_variant_eval_run(self, run: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO variant_eval_runs
                   (id, universe_id, gate_passed, gate_detail,
                    fitness_json, eval_set_version, cost, created_at, baseline_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run["id"],
                    run["universe_id"],
                    1 if run.get("gate_passed") else 0,
                    json.dumps(run.get("gate_detail"), ensure_ascii=False)
                    if run.get("gate_detail") is not None
                    else None,
                    json.dumps(run.get("fitness", {}), ensure_ascii=False),
                    run.get("eval_set_version"),
                    float(run.get("cost", 0.0)),
                    run["created_at"],
                    json.dumps(run["baseline"], ensure_ascii=False)
                    if run.get("baseline") is not None
                    else None,
                ),
            )

    def latest_baseline_fitness(self, eval_set_version: str) -> dict | None:
        """同评估集指纹下最近一次冠军基线分(供 evaluate_paired 缓存复用)。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT baseline_json FROM variant_eval_runs "
                "WHERE eval_set_version = ? AND baseline_json IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (eval_set_version,),
            ).fetchone()
        if not row or not row["baseline_json"]:
            return None
        try:
            return json.loads(row["baseline_json"])
        except (ValueError, TypeError):
            return None
```

`_row_to_eval_run`(文件顶部的行映射函数)增加:

```python
        "baseline": json.loads(r["baseline_json"]) if r["baseline_json"] else None,
```

(与该函数内 fitness_json 的既有解析风格保持一致,含异常防御则同样包一层。)

- [ ] **Step 5: 改造 propose_code_variant 为配对判定**

`evolver.py` propose_code_variant 中,评估段(原"eval_set_size = ... 到 margin 判定"整段)替换为:

```python
            eval_set_size = getattr(cfg, "code_variant_eval_set_size", 20)
            es = self._eval_harness.select_eval_set(eval_set_size)
            champion_key = self._mgr.champion_id() or "genesis"
            fp = self._eval_harness.eval_set_fingerprint(es, champion_key)
            cached = self._storage.latest_baseline_fitness(fp)
            budget = getattr(cfg, "code_variant_eval_budget_cny", None)
            paired = await asyncio.to_thread(
                self._eval_harness.evaluate_paired,
                worktree,
                eval_set=es,
                baseline_worktree=self._code_store.repo_root,
                budget_cny=budget,
                cached_baseline=cached,
            )
            fitness = paired["variant"]["fitness"]
            if paired["variant"].get("truncated"):
                fitness = {**fitness, "truncated": True}

            self._storage.save_variant_eval_run(
                {
                    "id": str(ULID()),
                    "universe_id": uid,
                    "gate_passed": True,
                    "fitness": fitness,
                    "baseline": paired["baseline"]["fitness"],
                    "eval_set_version": fp,
                    "cost": paired["variant"]["stats"].get("cost", 0),
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
            self._storage.update_universe_fitness(uid, fitness)

            margin = getattr(cfg, "universe_promote_margin", 0.05)
            if paired["delta"] >= margin:
                return {"status": "recommended", "universe_id": uid,
                        "fitness": fitness, "delta": paired["delta"]}
            return {"status": "evaluated", "universe_id": uid,
                    "fitness": fitness, "delta": paired["delta"]}
```

同步更新 `tests/universe/test_evolver_code.py`:fake eval_harness 需提供 `select_eval_set` / `eval_set_fingerprint` / `evaluate_paired`(返回上述结构),fake storage 需提供 `latest_baseline_fitness`(返回 None 即可);断言从 `champ_score + margin` 口径改为 `delta >= margin` 口径。

- [ ] **Step 6: 全量验证 + 提交**

Run: `.venv/bin/python -m pytest tests/universe -q && .venv/bin/python -m pytest -q -m "not slow" && .venv/bin/ruff check src tests`
Expected: 全绿

```bash
git add -A
git commit -m "feat(universe): 代码变体配对基线——冠军同集沙箱评估,margin 判配对差,基线按指纹缓存"
```

## Task 5: 行为层 challenger 沙箱配对评估

行为层变异(SOUL.md/ROLE.md 改写)此前从未被真正测量。本任务在 evolver.run 创建候选并落地变异后,立即用主仓代码 + env 覆盖 personas 目录跑沙箱配对评估:变体沙箱进程读 challenger 快照的 personas,基线读 live personas;delta 分流(劣→归档 / 优→推荐或自动晋升 / 平→留观)。

**Files:**
- Modify: `src/tianshu/universe/evolver.py`(run 接线 + `_evaluate_behavior_challenger`;EvolveResult 加 eval_delta)
- Test: `tests/universe/test_evolver.py`

**Interfaces:**
- Consumes: Task 2 `extra_env`、Task 3 `budget_cny`、Task 4 `evaluate_paired`/`eval_set_fingerprint`/`latest_baseline_fitness`/`repo_root`。
- Produces: `EvolveResult.eval_delta: float | None`(to_dict 同步);`universe.promotion_recommended` 事件 payload 含 `delta`。
- 前置事实(已核实):`bootstrap/wiring_persona.py:65` 用 `settings.runtime_personas_dir` 构造 PersonaLoader,pydantic-settings env 前缀 `TIANSHU_` → 沙箱进程内 `TIANSHU_RUNTIME_PERSONAS_DIR` 覆盖生效;`UniverseStore.personas_dir(id)` 返回 `{root}/{id}/personas`。

- [ ] **Step 1: 写失败测试(tests/universe/test_evolver.py 追加)**

沿用该文件既有的 fake manager/storage/config 风格,新增三条(核心逻辑:monkeypatch `_evaluate_behavior_challenger` 的下层依赖,不真拉沙箱):

```python
def _paired(delta, samples=20):
    v = {"fitness": {"score": 0.7 + delta, "samples": samples}, "stats": {"cost": 1.0},
         "n": samples, "truncated": False}
    b = {"fitness": {"score": 0.7, "samples": samples}, "stats": {"cost": 1.0},
         "n": samples, "truncated": False}
    return {"variant": v, "baseline": b, "delta": round(delta, 4), "baseline_cached": False}


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn


async def test_run_archives_challenger_on_negative_delta(evolver_fixture, monkeypatch):
    """delta ≤ -margin → 候选当场归档(retired)。"""
    evolver, mgr = evolver_fixture  # 按文件既有 fixture 命名对齐
    monkeypatch.setattr(evolver, "_evaluate_behavior_challenger",
                        _async_return(_paired(-0.1)))
    result = await evolver.run(trigger_source="manual")
    assert result.created_challenger in result.retired
    assert result.eval_delta == -0.1


async def test_run_recommends_on_positive_delta(evolver_fixture, monkeypatch):
    monkeypatch.setattr(evolver, "_evaluate_behavior_challenger",
                        _async_return(_paired(0.08)))
    result = await evolver.run(trigger_source="manual")
    assert result.promotion_recommended == result.created_challenger


async def test_run_keeps_challenger_in_margin_band(evolver_fixture, monkeypatch):
    monkeypatch.setattr(evolver, "_evaluate_behavior_challenger",
                        _async_return(_paired(0.02)))
    result = await evolver.run(trigger_source="manual")
    assert result.promotion_recommended is None
    assert result.retired == []
```

(fixture 需让 `_propose_mutation` 返回合法变异且 `apply_mutation` 成功——沿用文件内既有 mock 路数,以现文件为准调整。)

- [ ] **Step 2: 跑测试确认红**

Run: `.venv/bin/python -m pytest tests/universe/test_evolver.py -q -k "delta or margin_band"`
Expected: FAIL(`_evaluate_behavior_challenger` 不存在 / eval_delta 不存在)

- [ ] **Step 3: 实现**

`evolver.py`:

EvolveResult 加字段与 to_dict 键:

```python
    eval_delta: float | None = None
```

新方法(放 `_champion_summary` 之后):

```python
    async def _evaluate_behavior_challenger(self, child_id: str, cfg: Any) -> dict | None:
        """行为层候选的沙箱配对评估:主仓代码 + env 重定向 personas 到候选快照。

        协作者缺失(测试装配/未开代码变体基建)或无历史 goal 时返回 None,
        候选留观、不判晋升——失败安全,评估缺席不等于劣质。
        """
        if self._eval_harness is None or self._code_store is None:
            return None
        eval_set = self._eval_harness.select_eval_set(
            getattr(cfg, "code_variant_eval_set_size", 20))
        if not eval_set:
            return None
        store = self._mgr._store  # noqa: SLF001
        variant_env = {"TIANSHU_RUNTIME_PERSONAS_DIR": str(store.personas_dir(child_id))}
        champion_key = self._mgr.champion_id() or "genesis"
        fp = self._eval_harness.eval_set_fingerprint(eval_set, champion_key)
        cached = self._storage.latest_baseline_fitness(fp)
        budget = getattr(cfg, "code_variant_eval_budget_cny", None)
        repo_root = self._code_store.repo_root
        paired = await asyncio.to_thread(
            self._eval_harness.evaluate_paired,
            repo_root,
            eval_set=eval_set,
            baseline_worktree=repo_root,
            variant_env=variant_env,
            budget_cny=budget,
            cached_baseline=cached,
        )
        from datetime import UTC, datetime

        from ulid import ULID

        fitness = paired["variant"]["fitness"]
        self._storage.save_variant_eval_run(
            {
                "id": str(ULID()),
                "universe_id": child_id,
                "gate_passed": True,
                "fitness": fitness,
                "baseline": paired["baseline"]["fitness"],
                "eval_set_version": fp,
                "cost": paired["variant"]["stats"].get("cost", 0),
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        self._storage.update_universe_fitness(child_id, fitness)
        return paired
```

`run()` 的变异段之后(`result.mutation_detail = applied.get("detail")` 行后)接线:

```python
                if result.mutation_applied:
                    paired = await self._evaluate_behavior_challenger(child["id"], cfg)
                    if paired is not None:
                        margin = getattr(cfg, "universe_promote_margin", 0.05)
                        min_samples = getattr(cfg, "universe_min_samples", 20)
                        result.eval_delta = paired["delta"]
                        samples = paired["variant"]["fitness"].get("samples", 0)
                        if paired["delta"] <= -margin:
                            self._mgr.archive(child["id"])
                            result.retired.append(child["id"])
                        elif paired["delta"] >= margin and samples >= min_samples:
                            result.promotion_recommended = child["id"]
                            if getattr(cfg, "universe_auto_promote", False):
                                self._mgr.switch(child["id"])
                                await self._emit(
                                    "universe.promoted",
                                    {"universe_id": child["id"], "auto": True,
                                     "delta": paired["delta"]})
                            else:
                                await self._emit(
                                    "universe.promotion_recommended",
                                    {"universe_id": child["id"], "delta": paired["delta"],
                                     "samples": samples})
```

同时删除旧 `_maybe_promote` 方法及 run() 中 `result.promotion_recommended = await self._maybe_promote(champ, cfg)` 行(其"在线 fitness 对比"口径已被配对评估取代;历史遗留 challenger 由人工在 UI 处置),并同步清理 test_evolver.py 中 _maybe_promote 的旧用例。

- [ ] **Step 4: 全量验证 + 提交**

Run: `.venv/bin/python -m pytest tests/universe -q && .venv/bin/python -m pytest -q -m "not slow" && .venv/bin/ruff check src tests`
Expected: 全绿

```bash
git add -A
git commit -m "feat(universe): 行为层候选沙箱配对评估——变异首次被真正测量,delta 分流归档/推荐/留观"
```

---

# 批次 2:补脑——演化记忆与自主假设

## Task 6: 演化记忆——历史尝试进变异 prompt

`_propose_mutation` 的 prompt 只带当前 fitness 快照,LLM 会重复提出相似变异。本任务把历史变异台账(mutation origin 位面:理由/得分/结局)注入 prompt 并明确指示避开已试方向。

**Files:**
- Modify: `src/tianshu/universe/evolver.py`(`_USER` 模板 + `_mutation_history` + `_propose_mutation`)
- Test: `tests/universe/test_evolver.py`

**Interfaces:**
- Produces: `UniverseEvolver._mutation_history(limit: int = 20) -> str`(供 prompt 拼装;无历史时返回 `"(无历史尝试)"`)。

- [ ] **Step 1: 写失败测试**

```python
def test_mutation_history_lists_recent_attempts_with_outcome(evolver_fixture):
    evolver, mgr = evolver_fixture
    mgr._universes = [  # 按 fixture 实际造数方式对齐:两个 mutation 位面 + 一个 manual_branch
        {"id": "u-1", "origin": "mutation", "status": "archived",
         "mutation_reason": "ROLE 增加复核步骤", "name": "候选甲",
         "fitness": {"score": 0.61}, "created_at": "2026-07-01T00:00:00+00:00"},
        {"id": "u-2", "origin": "mutation", "status": "challenger",
         "mutation_reason": "SOUL 收紧输出格式", "name": "候选乙",
         "fitness": {"score": 0.74}, "created_at": "2026-07-02T00:00:00+00:00"},
        {"id": "u-3", "origin": "manual_branch", "status": "challenger",
         "mutation_reason": None, "name": "手动分支", "fitness": {},
         "created_at": "2026-07-03T00:00:00+00:00"},
    ]
    text = evolver._mutation_history()
    assert "ROLE 增加复核步骤" in text
    assert "SOUL 收紧输出格式" in text
    assert "手动分支" not in text            # 只列 mutation origin
    assert text.index("SOUL 收紧输出格式") < text.index("ROLE 增加复核步骤")  # 最近在前


def test_mutation_history_empty(evolver_fixture):
    evolver, mgr = evolver_fixture
    mgr._universes = []
    assert evolver._mutation_history() == "(无历史尝试)"
```

- [ ] **Step 2: 跑测试确认红**

Run: `.venv/bin/python -m pytest tests/universe/test_evolver.py -q -k mutation_history`
Expected: FAIL — no attribute `_mutation_history`

- [ ] **Step 3: 实现**

`evolver.py` 加方法:

```python
    def _mutation_history(self, limit: int = 20) -> str:
        """近期变异尝试台账(含已归档),供变异 prompt 避免重复方向。"""
        rows = [
            u for u in self._mgr.list(include_archived=True)
            if u.get("origin") == UniverseOrigin.MUTATION.value
        ]
        rows.sort(key=lambda u: u.get("created_at") or "", reverse=True)
        lines = []
        for u in rows[:limit]:
            f = u.get("fitness") or {}
            outcome = {"champion": "已晋升", "challenger": "留观中",
                       "archived": "已淘汰"}.get(u["status"], u["status"])
            lines.append(
                f"- [{outcome}] {u.get('mutation_reason') or u['name']}"
                f" (score={f.get('score', 'n/a')})")
        return "\n".join(lines) or "(无历史尝试)"
```

`_USER` 模板改为(在"冠军行为概要"段后插入历史段,并加避重指令):

```python
_USER = """\
冠军位面适应度:{champion_fitness}
各候选位面适应度:{challenger_fitness}
冠军行为概要(可改写的官员人格):
{summary}

近期已尝试过的变异(含结局,请勿重复相同或相近方向):
{history}

请提出【一处】可能让宫殿更贴合主上的人格改写,输出 JSON:
{{"target": "persona:<官员id>/ROLE.md 或 persona:<官员id>/SOUL.md(官员id 必须取自上面列出的官员)",
  "reason": "改这个文件的哪一点、为何可能更贴合(不得与已尝试方向重复)",
  "name": "候选位面名称(简短中文)"}}
若当前无明确可改之处,输出 {{"target": null, "reason": "...", "name": null}}。"""
```

`_propose_mutation` 的 prompt 拼装同步加 `history=self._mutation_history()`。

- [ ] **Step 4: 验证 + 提交**

Run: `.venv/bin/python -m pytest tests/universe/test_evolver.py -q && .venv/bin/ruff check src tests`
Expected: PASS

```bash
git add -A
git commit -m "feat(universe): 演化记忆——历史变异台账进 prompt,避免重复踩坑"
```

## Task 7: 太医诊断器——失败症状 → 代码演化假设

代码层此前只能人工给假设,平台坐拥失败奏折/审计意见/错误信息却没有喂给演化引擎。新增 Diagnostician:采集近期失败症状与已试假设,LLM 提炼演化域内的代码改进假设清单。

**Files:**
- Create: `src/tianshu/universe/diagnostician.py`
- Test: `tests/universe/test_diagnostician.py`(新建)

**Interfaces:**
- Produces: `Diagnostician(llm_client, storage, *, evolvable_paths: tuple[str, ...])`;`async diagnose(*, max_hypotheses: int = 3) -> list[dict]`,每项 `{"target_path": str, "hypothesis": str, "rationale": str}`,target_path 保证落在 evolvable allowlist 内;无失败症状或 LLM 三试失败返回 `[]`(失败安全)。
- Consumes: `storage.list_memorials(status="failed", limit=...)`(memorial_repo.py:104,已存在)、`storage.get_edict(id)`、`storage.list_universes(include_archived=True)`、`code_mutator._within_evolvable`。

- [ ] **Step 1: 写失败测试(新建 tests/universe/test_diagnostician.py)**

```python
"""Diagnostician:失败症状采集、已试假设去重、allowlist 过滤、失败安全。"""

import json

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
    def __init__(self, edict_id, error=None, audit_json=None):
        self.edict_id = edict_id
        self.error = error
        self.audit_json = audit_json


class _FakeStorage:
    def __init__(self, memorials=(), universes=()):
        self._mems = list(memorials)
        self._unis = list(universes)

    def list_memorials(self, status=None, limit=50, offset=0):
        return self._mems[:limit]

    def get_edict(self, edict_id):
        return _FakeEdict(goal=f"目标-{edict_id}")

    def list_universes(self, include_archived=True):
        return self._unis


def _diag(payload, memorials=(), universes=()):
    return Diagnostician(
        _FakeLLM(payload), _FakeStorage(memorials, universes),
        evolvable_paths=("src/tianshu/planner/", "src/tianshu/tools/http.py"))


async def test_diagnose_returns_allowlisted_hypotheses():
    payload = json.dumps([
        {"target_path": "src/tianshu/planner/planner.py",
         "hypothesis": "拆解超长目标时先分段", "rationale": "3 条超时失败"},
        {"target_path": "src/tianshu/executor/agent.py",   # 演化域外 → 过滤
         "hypothesis": "越界提案", "rationale": "x"},
    ])
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
    unis = [{"origin": "code_variant", "description": "已试:planner 分段",
             "created_at": "2026-07-01T00:00:00+00:00"}]
    diag = _diag(payload, mems, unis)
    await diag.diagnose()
    prompt = diag._llm.prompts[0]
    assert "TimeoutError" in prompt
    assert "已试:planner 分段" in prompt


async def test_diagnose_bad_llm_output_fails_safe():
    mems = [_FakeMemorial("e1", error="boom")]
    result = await _diag("这不是 JSON", mems).diagnose()
    assert result == []
```

- [ ] **Step 2: 跑测试确认红**

Run: `.venv/bin/python -m pytest tests/universe/test_diagnostician.py -q`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 diagnostician.py**

```python
"""Diagnostician(太医)— 从失败奏折与审计意见中提炼代码演化假设。

只诊断、不动刀:输出演化域内的 (target_path, hypothesis) 清单,
交由 UniverseEvolver.propose_code_variant 走既有「分支→变异→门禁→评估」闭环。
失败安全:无症状 / LLM 输出非法 / 全部越界 → 返回空列表。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from tianshu.universe.code_mutator import _within_evolvable

logger = logging.getLogger(__name__)

_SYSTEM = (
    "你是天枢的「太医」,负责诊断平台自身代码的病灶。"
    "给定近期失败任务的症状(目标/错误/审计意见)与已尝试过的假设,"
    "提出最多 {k} 条新的代码改进假设,每条瞄准演化域内的一个文件,"
    "严禁与已尝试假设方向重复。只输出 JSON 数组,不带 markdown 代码块标记。"
)

_USER = """\
近期失败症状:
{failures}

已尝试过的假设(避免重复方向):
{tried}

演化域(target_path 必须落在其中;目录以 / 结尾表示前缀):
{evolvable}

输出 JSON 数组,每项:
{{"target_path": "src/tianshu/...", "hypothesis": "改什么、为何能减少上述失败", "rationale": "对应哪些症状"}}
无可提之处输出 []。"""


class Diagnostician:
    def __init__(
        self, llm_client: Any, storage: Any, *, evolvable_paths: tuple[str, ...]
    ) -> None:
        self._llm = llm_client
        self._storage = storage
        self._evolvable = tuple(evolvable_paths)

    async def diagnose(self, *, max_hypotheses: int = 3) -> list[dict]:
        failures = self._collect_failures()
        if not failures:
            return []
        prompt = _USER.format(
            failures=failures,
            tried=self._tried_hypotheses(),
            evolvable="\n".join(f"- {p}" for p in self._evolvable),
        )
        raw = await self._ask_llm(prompt, max_hypotheses)
        if not isinstance(raw, list):
            return []
        out: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            target = str(item.get("target_path") or "")
            hypothesis = str(item.get("hypothesis") or "").strip()
            if not target or not hypothesis:
                continue
            if not _within_evolvable(target, self._evolvable):
                logger.info("diagnose: drop out-of-evolvable proposal %s", target)
                continue
            out.append(
                {"target_path": target, "hypothesis": hypothesis,
                 "rationale": str(item.get("rationale") or "")}
            )
            if len(out) >= max_hypotheses:
                break
        return out

    def _collect_failures(self, limit: int = 30) -> str:
        """近期失败 memorial 的症状行:goal / error / 审计意见。"""
        try:
            mems = self._storage.list_memorials(status="failed", limit=limit)
        except Exception:  # noqa: BLE001
            logger.warning("diagnose: list_memorials failed", exc_info=True)
            return ""
        lines: list[str] = []
        for m in mems:
            edict = self._storage.get_edict(m.edict_id)
            goal = (getattr(edict, "goal", "") or "")[:120]
            err = (getattr(m, "error", "") or "")[:200]
            audit = ""
            aj = getattr(m, "audit_json", None)
            if isinstance(aj, dict):
                audit = "; ".join(str(r) for r in aj.get("reasons", []))[:200]
            lines.append(f"- goal: {goal}\n  error: {err}\n  audit: {audit}")
        return "\n".join(lines)

    def _tried_hypotheses(self, limit: int = 20) -> str:
        unis = self._storage.list_universes(include_archived=True)
        rows = [
            u for u in unis
            if u.get("origin") == "code_variant" and u.get("description")
        ]
        rows.sort(key=lambda u: u.get("created_at") or "", reverse=True)
        return "\n".join(f"- {u['description'][:150]}" for u in rows[:limit]) or "(无)"

    async def _ask_llm(self, prompt: str, k: int) -> Any:
        messages = [
            {"role": "system", "content": _SYSTEM.format(k=k)},
            {"role": "user", "content": prompt},
        ]
        for _ in range(3):
            try:
                resp = await self._llm.chat(messages=messages)
                text = (getattr(resp, "content", None) or "").strip()
                if text.startswith("```") and "\n" in text:
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                return json.loads(text)
            except (json.JSONDecodeError, ValueError):
                prompt += "\n\n上次输出非合法 JSON,严格只输出 JSON 数组。"
                messages[-1] = {"role": "user", "content": prompt}
            except Exception:  # noqa: BLE001
                await asyncio.sleep(1)
        return []
```

- [ ] **Step 4: 验证 + 提交**

Run: `.venv/bin/python -m pytest tests/universe/test_diagnostician.py -q && .venv/bin/ruff check src tests`
Expected: PASS

```bash
git add -A
git commit -m "feat(universe): 太医诊断器——失败奏折/审计意见提炼演化域内代码假设"
```

## Task 8: 自主提案接线——配额、cron、API

把诊断器接进演化引擎:`auto_propose_codes` 在配额内逐个走既有 propose 闭环;每日 cron 05:30 自动触发(默认关);手动 API 一键触发。

**Files:**
- Modify: `src/tianshu/universe/evolver.py`(`auto_propose_codes` + `__init__` 加 diagnostician)
- Modify: `src/tianshu/config_manager.py`(加 `code_variant_auto_propose: bool = False`、`code_variant_daily_propose_quota: int = 2`)
- Modify: `src/tianshu/bootstrap/wiring_universe.py`(组装 Diagnostician 注入)
- Modify: `src/tianshu/scheduler/scheduler.py`(register_system_jobs 注册 `universe.daily_code_propose`)
- Modify: `src/tianshu/gateway/universes_api.py`(`POST /universes/propose-auto`)
- Test: `tests/universe/test_evolver_code.py`、`tests/universe/test_universe_api.py`

**Interfaces:**
- Produces: `UniverseEvolver.auto_propose_codes(trigger_source: str = "cron") -> dict`,返回 `{"skipped": <原因>}` 或 `{"proposed": int, "results": [{"proposal": {...}, "result": {...}}]}`;发事件 `universe.code_proposed`。
- Produces: 路由 `POST /api/universes/propose-auto`(唯一允许的新增路由)。

- [ ] **Step 1: 写失败测试(tests/universe/test_evolver_code.py 追加)**

```python
async def test_auto_propose_respects_quota_and_disabled(evolver_code_fixture, monkeypatch):
    evolver = evolver_code_fixture  # 按文件既有 fixture 对齐;cfg.code_variant_enabled=True

    # 关开关 → skipped
    evolver._config.agent_config.code_variant_auto_propose = False
    assert (await evolver.auto_propose_codes())["skipped"] == "disabled"

    # 开开关:诊断出 3 条,quota=2 → 只提 2 个
    evolver._config.agent_config.code_variant_auto_propose = True
    evolver._config.agent_config.code_variant_daily_propose_quota = 2

    class _FakeDiag:
        async def diagnose(self, *, max_hypotheses):
            assert max_hypotheses == 2
            return [{"target_path": "src/tianshu/planner/p.py", "hypothesis": f"h{i}",
                     "rationale": ""} for i in range(max_hypotheses)]

    evolver._diagnostician = _FakeDiag()
    proposed = []

    async def _fake_propose(*, target_path, hypothesis, parent_id=None):
        proposed.append(hypothesis)
        return {"status": "evaluated", "universe_id": f"u-{hypothesis}"}

    monkeypatch.setattr(evolver, "propose_code_variant", _fake_propose)
    out = await evolver.auto_propose_codes(trigger_source="manual")
    assert out["proposed"] == 2
    assert proposed == ["h0", "h1"]


async def test_auto_propose_no_diagnostician_skips(evolver_code_fixture):
    evolver = evolver_code_fixture
    evolver._config.agent_config.code_variant_auto_propose = True
    evolver._diagnostician = None
    assert (await evolver.auto_propose_codes())["skipped"] == "no_diagnostician"
```

- [ ] **Step 2: 跑测试确认红**

Run: `.venv/bin/python -m pytest tests/universe/test_evolver_code.py -q -k auto_propose`
Expected: FAIL — no attribute `auto_propose_codes`

- [ ] **Step 3: 实现**

`config_manager.py` AgentConfig(紧随 `code_variant_eval_budget_cny` 之后):

```python
    code_variant_auto_propose: bool = False
    code_variant_daily_propose_quota: int = 2
```

`evolver.py`:模块级加 `_CODE_LOCK_KEY = "__universe_code_propose__"`;`__init__` 参数列表末尾加 `diagnostician: Any = None`,体内 `self._diagnostician = diagnostician`;新方法:

```python
    async def auto_propose_codes(self, trigger_source: str = "cron") -> dict:
        """自主代码提案:太医诊断 → 配额内逐个走 propose 闭环。失败安全。"""
        cfg = self._config.agent_config
        if not getattr(cfg, "code_variant_enabled", False) or not getattr(
            cfg, "code_variant_auto_propose", False
        ):
            return {"skipped": "disabled"}
        if self._diagnostician is None:
            return {"skipped": "no_diagnostician"}
        if trigger_source != "manual" and not self._idle_ok(
            getattr(cfg, "universe_evolver_idle_hours", 2)
        ):
            return {"skipped": "not_idle"}
        if not self._storage.try_acquire_synthesis_lock(_CODE_LOCK_KEY):
            return {"skipped": "lock_held"}
        try:
            quota = max(0, getattr(cfg, "code_variant_daily_propose_quota", 2))
            proposals = await self._diagnostician.diagnose(max_hypotheses=quota)
            results = []
            for p in proposals[:quota]:
                r = await self.propose_code_variant(
                    target_path=p["target_path"], hypothesis=p["hypothesis"]
                )
                results.append({"proposal": p, "result": r})
            out = {"proposed": len(results), "results": results}
            await self._emit("universe.code_proposed", out)
            return out
        except Exception as e:  # noqa: BLE001
            logger.exception("[EVOLVER] auto_propose_codes failed")
            return {"skipped": "error", "detail": str(e)}
        finally:
            self._storage.release_synthesis_lock(_CODE_LOCK_KEY)
```

`wiring_universe.py`:import Diagnostician,在 `code_mutator = ...` 之后:

```python
    diagnostician = Diagnostician(
        provider_manager.get_client(),
        storage,
        evolvable_paths=_cfg.code_variant_evolvable_paths,
    )
```

UniverseEvolver 构造加 `diagnostician=diagnostician`。

`scheduler/scheduler.py` register_system_jobs 的 `if universe_evolver is not None:` 块内追加(与 `_fire_evolve` 并列):

```python
            async def _fire_code_propose() -> None:
                await universe_evolver.auto_propose_codes(trigger_source="cron")

            self._system_jobs.append(
                {"cron": "30 5 * * *", "name": "universe.daily_code_propose",
                 "fn": _fire_code_propose}
            )
            logger.info("Registered system job: universe.daily_code_propose (30 5 * * *)")
```

`gateway/universes_api.py` 在 `trigger_evolve` 后追加(注意放在所有 `/{universe_id}` 参数路由**之前**,防影子路由):

```python
@universes_router.post("/propose-auto", response_model=ApiResponse)
async def trigger_auto_propose(request: Request):
    evolver = request.app.state.universe_evolver
    result = await evolver.auto_propose_codes(trigger_source="manual")
    return ApiResponse(success=True, data=result)
```

`tests/universe/test_universe_api.py` 追加一条 smoke(fake evolver 返回 `{"skipped": "disabled"}`,断言 200 + data 透传,沿用文件既有 app fixture)。

- [ ] **Step 4: 全量验证 + 提交**

Run: `.venv/bin/python -m pytest tests/universe -q && .venv/bin/python -m pytest -q -m "not slow" && .venv/bin/ruff check src tests && .venv/bin/python scripts/dump_routes.py | grep propose-auto`
Expected: 全绿;新路由仅 `POST /api/universes/propose-auto`

```bash
git add -A
git commit -m "feat(universe): 自主代码提案闭环——诊断→配额内提案,cron 05:30 + 手动 API"
```

---

# 批次 3:加固与信任面

## Task 9: 评估集分层——成功 + 失败混采

现状评估集只取最近成功 goal,变体永远无法证明"修好了冠军跑不动的"。改为约 60% 成功 + 40% 失败混采(不足互补);配对基线下冠军在失败样本上同样失败,变体修好即体现为正 delta。

**Files:**
- Modify: `src/tianshu/universe/eval_harness.py`(select_eval_set 重写 + `_collect_goals`)
- Test: `tests/universe/test_eval_harness.py`

**Interfaces:**
- Produces: `select_eval_set(size) -> list[str]` 签名不变;内部新增 `_collect_goals(status, want, exclude=None) -> list[str]`。
- 前置确认:`Edict` 状态含 `failed`(models/edict.py 状态枚举;若终态命名不同以模型为准同步调整)。

- [ ] **Step 1: 写失败测试**

```python
def test_select_eval_set_mixes_failed_goals():
    from tianshu.universe.eval_harness import EvalHarness

    class _E:
        def __init__(self, goal):
            self.goal = goal

    class _FakeStorage:
        def list_edicts(self, status=None, limit=50, **kw):
            if status == "completed":
                return [_E(f"成功目标{i}") for i in range(20)], 20
            if status == "failed":
                return [_E(f"失败目标{i}") for i in range(20)], 20
            return [], 0

    harness = EvalHarness(storage=_FakeStorage(), sandbox_runner=None)
    goals = harness.select_eval_set(10)
    assert len(goals) == 10
    assert sum(1 for g in goals if g.startswith("失败")) == 4   # 40%
    assert sum(1 for g in goals if g.startswith("成功")) == 6


def test_select_eval_set_backfills_when_failed_scarce():
    """失败样本不足时用成功样本回填到满额。"""
    from tianshu.universe.eval_harness import EvalHarness

    class _E:
        def __init__(self, goal):
            self.goal = goal

    class _FakeStorage:
        def list_edicts(self, status=None, limit=50, **kw):
            if status == "completed":
                return [_E(f"成功目标{i}") for i in range(20)], 20
            if status == "failed":
                return [_E("失败目标0")], 1
            return [], 0

    goals = EvalHarness(storage=_FakeStorage(), sandbox_runner=None).select_eval_set(10)
    assert len(goals) == 10
    assert sum(1 for g in goals if g.startswith("失败")) == 1
    assert sum(1 for g in goals if g.startswith("成功")) == 9


def test_select_eval_set_dedupes_across_strata():
    """同一 goal 同时出现在成功与失败层时不重复入集。"""
    from tianshu.universe.eval_harness import EvalHarness

    class _E:
        def __init__(self, goal):
            self.goal = goal

    class _FakeStorage:
        def list_edicts(self, status=None, limit=50, **kw):
            if status == "completed":
                return [_E("重叠目标"), _E("成功目标1"), _E("成功目标2")], 3
            if status == "failed":
                return [_E("重叠目标"), _E("失败目标1")], 2
            return [], 0

    goals = EvalHarness(storage=_FakeStorage(), sandbox_runner=None).select_eval_set(5)
    assert len(goals) == len(set(goals))  # 无重复
    assert "重叠目标" in goals
```

- [ ] **Step 2: 跑测试确认红**

Run: `.venv/bin/python -m pytest tests/universe/test_eval_harness.py -q -k select_eval_set`
Expected: 新用例 FAIL(现实现全取 completed)

- [ ] **Step 3: 实现**

```python
    def select_eval_set(self, size: int) -> list[str]:
        """分层选集:约 60% 最近成功 + 40% 最近失败(跨层去重,不足互补)。

        含失败样本让变体有机会证明「修好了冠军跑不动的」——配对基线下
        冠军在失败样本上同样失败,变体修好即体现为正 delta。
        """
        n_fail = int(size * 0.4)
        fail_goals = self._collect_goals("failed", n_fail)
        succ_goals = self._collect_goals(
            "completed", size - len(fail_goals), exclude=set(fail_goals))
        short = size - len(succ_goals) - len(fail_goals)
        if short > 0:
            fail_goals.extend(self._collect_goals(
                "failed", short, exclude=set(fail_goals) | set(succ_goals)))
        return succ_goals + fail_goals

    def _collect_goals(
        self, status: str, want: int, exclude: set[str] | None = None
    ) -> list[str]:
        if want <= 0:
            return []
        seen: set[str] = set(exclude or ())
        edicts, _ = self._storage.list_edicts(status=status, limit=want * 3)
        goals: list[str] = []
        for e in edicts:
            g = e.goal.strip()
            if g and g not in seen:
                seen.add(g)
                goals.append(g)
            if len(goals) >= want:
                break
        return goals
```

旧 select_eval_set 的既有测试若按"只取 completed"断言,同步改为新口径。

- [ ] **Step 4: 验证 + 提交**

Run: `.venv/bin/python -m pytest tests/universe/test_eval_harness.py -q && .venv/bin/ruff check src tests`
Expected: PASS

```bash
git add -A
git commit -m "feat(universe): 评估集分层混采——60% 成功 + 40% 失败,变体可证明修复能力"
```

## Task 10: eval 凭证隔离——沙箱用低额度 LLM key

live 评估下 untrusted 变体进程持有真实 LLM key(设计文档 §10 自认的 🔴 残余风险)。提供 `TIANSHU_EVAL_LLM_*` 三配置,配置后沙箱进程的 LLM 走独立低额度凭证。

**Files:**
- Modify: `src/tianshu/config.py`(TianshuSettings 加三字段)
- Modify: `src/tianshu/universe/eval_harness.py`(__init__ 加 base_env;evaluate 合并)
- Modify: `src/tianshu/bootstrap/wiring_universe.py`(组装 base_env)
- Test: `tests/universe/test_eval_harness.py`

**Interfaces:**
- Produces: `TianshuSettings.eval_llm_api_key/eval_llm_api_base/eval_llm_model`(str,默认 "");`EvalHarness(storage, sandbox_runner, *, fitness_weights=..., base_env: dict[str, str] | None = None)`。
- 机制:沙箱进程的 LLM 默认配置来自其空 DB → TianshuSettings → 继承宿主 env 的 `TIANSHU_LLM_*`;base_env 覆盖这三个 env 即完成凭证切换。

- [ ] **Step 1: 写失败测试**

```python
def test_evaluate_merges_base_env_into_sandbox(tmp_path, monkeypatch):
    from tianshu.universe.eval_harness import EvalHarness

    captured = {}

    class _H:
        base_url = "http://x"
        db_path = tmp_path / "_eval.db"

    class _FakeSandbox:
        import contextlib

        @contextlib.contextmanager
        def session(self, worktree, *, db_path, extra_env=None):
            captured["extra_env"] = extra_env
            yield _H()

    harness = EvalHarness(
        storage=None, sandbox_runner=_FakeSandbox(),
        base_env={"TIANSHU_LLM_API_KEY": "sk-eval-low"})
    monkeypatch.setattr(harness, "_run_goal", lambda *a: None)
    monkeypatch.setattr(harness, "aggregate_db_stats", lambda db: {
        "total": 0, "success": 0, "retries": 0, "audited": 0,
        "audit_pass": 0, "cost": 0.0, "feedback": 0})

    harness.evaluate(tmp_path, eval_set=["g"],
                     extra_env={"TIANSHU_RUNTIME_PERSONAS_DIR": "/tmp/p"})
    assert captured["extra_env"]["TIANSHU_LLM_API_KEY"] == "sk-eval-low"
    assert captured["extra_env"]["TIANSHU_RUNTIME_PERSONAS_DIR"] == "/tmp/p"
```

- [ ] **Step 2: 跑测试确认红**

Run: `.venv/bin/python -m pytest tests/universe/test_eval_harness.py -q -k base_env`
Expected: FAIL — `__init__() got an unexpected keyword argument 'base_env'`

- [ ] **Step 3: 实现**

`config.py` 在 `eval_mode` 之后:

```python
    # 沙箱评估专用 LLM 凭证(空 = 沿用宿主凭证)。untrusted 变体进程在评估期
    # 能拿到 LLM key,配置低额度专用 key 可把泄漏面压到额度上限。
    eval_llm_api_key: str = ""
    eval_llm_api_base: str = ""
    eval_llm_model: str = ""
```

`eval_harness.py`:

```python
    def __init__(
        self, storage, sandbox_runner, *,
        fitness_weights=(0.4, 0.15, 0.2, 0.1, 0.15),
        base_env: dict[str, str] | None = None,
    ):
        self._storage = storage
        self._sandbox = sandbox_runner
        self._weights = fitness_weights
        self._base_env = dict(base_env or {})
```

`evaluate` 里 session 调用前合并(调用点传入的 extra_env 优先):

```python
        merged_env = {**self._base_env, **(extra_env or {})} or None
        with self._sandbox.session(worktree, db_path=iso_db, extra_env=merged_env) as h:
```

`wiring_universe.py` EvalHarness 组装处:

```python
    eval_base_env: dict[str, str] = {}
    if settings.eval_llm_api_key:
        eval_base_env["TIANSHU_LLM_API_KEY"] = settings.eval_llm_api_key
        if settings.eval_llm_api_base:
            eval_base_env["TIANSHU_LLM_API_BASE"] = settings.eval_llm_api_base
        if settings.eval_llm_model:
            eval_base_env["TIANSHU_LLM_MODEL"] = settings.eval_llm_model
    code_eval_harness = EvalHarness(
        storage,
        code_sandbox,
        fitness_weights=_cfg.universe_fitness_weights,
        base_env=eval_base_env,
    )
```

- [ ] **Step 4: 验证 + 提交**

Run: `.venv/bin/python -m pytest tests/universe -q && .venv/bin/ruff check src tests`
Expected: PASS

```bash
git add -A
git commit -m "feat(universe): 沙箱评估凭证隔离——TIANSHU_EVAL_LLM_* 低额度专用 key"
```

## Task 11: 前端信任面——谱系树 + 晋升审批视图

晋升前人工审核是安全压舱石,但现状 diff 与评估记录分散在两个 Modal。本任务:①位面列表上方加谱系树(parent_universe_id 已有);②"晋升"按钮改为打开审批 Modal——diff、评估记录(含基线分/delta 列)同屏,确认后才真正晋升。

**Files:**
- Modify: `web/src/api/types.ts`(VariantEvalRun 加 `baseline`)
- Modify: `web/src/api/universe.ts`(加 `proposeAutoCode`)
- Modify: `web/src/pages/UniversePage.tsx`
- 验证: `cd web && npx tsc --noEmit && npm run build`

**Interfaces:**
- Consumes: Task 4 的 `list_variant_eval_runs` 返回行含 `baseline`(dict | null);Task 8 的 `POST /api/universes/propose-auto`。
- 页面文案与现状一致用硬编码中文(不扩 i18n,见"明确不做")。

- [ ] **Step 1: types.ts 与 api 层**

`web/src/api/types.ts` 的 `VariantEvalRun` 接口加字段(对齐现有字段命名风格):

```typescript
  baseline?: Record<string, number> | null;
```

`web/src/api/universe.ts` 追加:

```typescript
export async function proposeAutoCode(): Promise<ApiResponse<Record<string, unknown>>> {
  const { data } = await apiClient.post<ApiResponse<Record<string, unknown>>>(
    "/universes/propose-auto",
  );
  return data;
}
```

- [ ] **Step 2: 谱系树**

`UniversePage.tsx`:import `Tree`(antd)与 `type { DataNode }`。组件内加构树函数与折叠 Card(表格上方渲染;实现时对照现文件 JSX 结构落位):

```tsx
function buildLineage(rows: Universe[]): DataNode[] {
  const byParent = new Map<string | null, Universe[]>();
  rows.forEach((u) => {
    const k = u.parent_universe_id ?? null;
    byParent.set(k, [...(byParent.get(k) ?? []), u]);
  });
  const toNode = (u: Universe): DataNode => ({
    key: u.id,
    title: `${u.name} · ${STATUS_LABEL[u.status] ?? u.status} · score=${
      u.fitness?.score ?? "—"
    }`,
    children: (byParent.get(u.id) ?? []).map(toNode),
  });
  return (byParent.get(null) ?? []).map(toNode);
}
```

渲染(rows 非空时):

```tsx
<Card size="small" title="位面谱系" style={{ marginBottom: 16 }}>
  <Tree treeData={buildLineage(rows)} defaultExpandAll showLine />
</Card>
```

(`Card` 自 antd import;`Universe` 类型的 `fitness` 若为 string 需按 types.ts 实际定义解析,以现文件对 fitness 的既有用法为准。)

- [ ] **Step 3: 晋升审批 Modal**

现状"晋升"按钮直接调 `promoteCodeVariant`(以现文件为准定位)。改为:点击先打开审批 Modal,diff 与评估记录同屏,确认后才真正晋升。

状态与打开逻辑(命名对齐现文件 modal state 风格;diff/evalRuns 复用现有两个 Modal 的加载函数,打开时并行拉取):

```tsx
const [promoteReviewOpen, setPromoteReviewOpen] = useState(false);
const [promoteTarget, setPromoteTarget] = useState<Universe | null>(null);
const [promoting, setPromoting] = useState(false);

const onOpenPromoteReview = async (u: Universe) => {
  setPromoteTarget(u);
  setPromoteReviewOpen(true);
  await Promise.all([loadDiff(u), loadEvalRuns(u)]); // 以现文件既有加载函数名为准
};

const onConfirmPromote = async () => {
  if (!promoteTarget) return;
  setPromoting(true);
  try {
    const res = await promoteCodeVariant(promoteTarget.id);
    if (res.success) {
      void message.success("已晋升为冠军并暂存部署指针(重启为受控步骤)");
      setPromoteReviewOpen(false);
      void load();
    }
  } finally {
    setPromoting(false);
  }
};
```

审批 Modal JSX(评估表 delta 列绿正红负;`fitness` 字段的取值方式以 types.ts 实际定义为准):

```tsx
<Modal
  title={`晋升审批:${promoteTarget?.name ?? ""}`}
  open={promoteReviewOpen}
  onCancel={() => setPromoteReviewOpen(false)}
  width={960}
  footer={[
    <Button key="cancel" onClick={() => setPromoteReviewOpen(false)}>取消</Button>,
    <Button key="ok" type="primary" danger loading={promoting} onClick={onConfirmPromote}>
      确认晋升
    </Button>,
  ]}
>
  <h4>代码改动(相对 fork 起点)</h4>
  <pre style={{ maxHeight: 320, overflow: "auto", background: "#f6f6f6", padding: 12 }}>
    {diffContent || "(无改动或加载中)"}
  </pre>
  <h4>评估记录(变体 vs 冠军基线,同评估集)</h4>
  <Table<VariantEvalRun>
    size="small"
    rowKey="id"
    pagination={false}
    dataSource={evalRuns}
    columns={[
      { title: "时间", dataIndex: "created_at", width: 180 },
      { title: "门禁", dataIndex: "gate_passed", width: 70,
        render: (v: boolean) => (v ? <Tag color="green">过</Tag> : <Tag color="red">毙</Tag>) },
      { title: "变体分", width: 90, render: (_, r) => r.fitness?.score ?? "—" },
      { title: "基线分", width: 90, render: (_, r) => r.baseline?.score ?? "—" },
      { title: "delta", width: 90,
        render: (_, r) => {
          const v = r.fitness?.score, b = r.baseline?.score;
          if (typeof v !== "number" || typeof b !== "number") return "—";
          const d = v - b;
          return <span style={{ color: d >= 0 ? "#3f8600" : "#cf1322" }}>{d.toFixed(4)}</span>;
        } },
      { title: "备注", render: (_, r) => (r.fitness?.truncated ? "预算截断" : "") },
    ]}
  />
  <p style={{ marginTop: 12, color: "#999" }}>
    晋升将翻转冠军并暂存部署指针;重启是单独受控步骤,健康检查失败会自动回滚。
  </p>
</Modal>
```

工具栏加"自主提案"按钮(调 `proposeAutoCode`,结果 message 提示 `data.skipped ? \`跳过:${data.skipped}\` : \`已提案 ${data.proposed} 个\``)。

- [ ] **Step 4: 验证 + 提交**

Run: `cd web && npx tsc --noEmit && npm run build`
Expected: 零 TS 错误,build 成功

```bash
git add -A
git commit -m "feat(web): 位面谱系树 + 晋升审批视图(diff/评估/基线同屏)+ 自主提案入口"
```

## Task 12: 文档同步

**Files:**
- Modify: `docs/design/universe/evolution.md`、`docs/design/universe/eval.md`、`docs/design/universe/code-variant.md`、`docs/impl/universe/README.md`

**改点清单(逐条落实,保持古风文风):**

- [ ] `evolution.md`:§5 演化步骤表加"沙箱配对评估 + delta 分流"环节、删"熔断"行(改为评估分流);§6 加"变异 prompt 携带历史台账";§8 探索路由改写为"已退役——challenger 不真正运行,归因一律冠军;per-run 装配落地后再恢复"(保留原设计意图一句);§9 配置表删 `universe_explore_ratio`/`universe_challenger_fail_limit`,`universe_min_samples` 语义改为"配对评估参与晋升的最低样本数"。
- [ ] `eval.md`:§2 选集改分层混采口径;§5 回归比对改"同集配对基线 + delta,基线按指纹缓存";§8 配置表加 `code_variant_eval_budget_cny`、`TIANSHU_EVAL_LLM_*` 三项;新增一小节"预算闸与截断语义"。
- [ ] `code-variant.md`:§6 后新增"§6b 太医诊断器与自主提案"(诊断输入/allowlist 过滤/配额/cron 05:30/默认关);§9 配置表加 `code_variant_auto_propose`/`code_variant_daily_propose_quota`/`code_variant_eval_budget_cny`;§10 安全主防线补"eval 专用低额度凭证"一行,🔴 残余风险改为"可通过 TIANSHU_EVAL_LLM_* 压缩泄漏面"。
- [ ] `docs/impl/universe/README.md`:§1 模块清单加 `diagnostician.py` 行;§5.1 诏令归因改"一律归冠军(探索路由已退役)";§5.3 闭环描述补配对基线与预算闸;§7 扩展点表同步。
- [ ] 全 docs 扫尾:`grep -rn "explore_ratio\|challenger_fail_limit" docs/ --include="*.md"`,现状类文档清零(docs/plan、docs/superpowers 历史记录豁免)。

- [ ] **验证 + 提交**

Run: `grep -rn "explore_ratio" docs/design docs/impl docs/usage README.md; echo "exit=$?"`
Expected: 无输出(exit=1)

```bash
git add -A
git commit -m "docs(universe): 同步配对评估/探索退役/诊断器/预算闸/凭证隔离"
```

---

# 风险与已知边界(执行前知悉)

1. **行为层评估耗时**:一次配对评估 = 最多两次沙箱回放(每次 ≤ eval_set_size × goal_timeout_s 串行);cron 05:00/05:30 低峰执行可接受,但手动 `POST /universes/evolve` 会长阻塞——与现状 `propose-code` 行为一致,本轮不改交互模式。
2. **评估烧真实 LLM 费用**:预算闸(T3)默认 20 元/次评估;基线缓存(T4)命中时减半。首次启用建议把 `code_variant_eval_set_size` 调小观察。
3. **沙箱进程会启动完整 app**(含 scheduler 注册),`TIANSHU_EVAL_MODE=1` 已围栏外发副作用;评估窗口不跨 cron 触发点,风险低。
4. **行为层评估只重定向 personas 目录**:skills 目录与 manifest config 的变异本就未落地(mutator 只改 SOUL/ROLE),不影响正确性;将来扩变异面时需同步扩 env 重定向。
5. **`universe_min_samples` 语义迁移**:从"在线样本量"变为"配对评估样本量",默认 20 与 eval_set_size 默认一致;用户若调小 eval_set_size 需同步调小 min_samples,文档已注明。

# 收官验证(全部批次完成后)

- [ ] `.venv/bin/python -m pytest -q -m "not slow"` 全绿,总数 ≥ 1364 + 新增用例数 - 删除的 retire/routing 旧用例数;
- [ ] `.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests` 双净;
- [ ] `.venv/bin/python scripts/dump_routes.py` 对照:仅新增 `POST /api/universes/propose-auto`;
- [ ] `cd web && npx tsc --noEmit && npm run build` 通过;
- [ ] 手工冒烟(用户验收):UniversePage 谱系树渲染、晋升审批 Modal(diff+评估+基线同屏)、自主提案按钮(未开开关时提示 disabled)。
