# 工程治理三批次实现计划(卫生/边界/类型与覆盖)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落实 codex review 经核实的真问题:统一质量门禁口径、补齐资源生命周期、让 `tianshu[telegram]` 真正独立于 feishu、用 import-linter 固化分层防回归、mypy 扩到五个横向包、补三个裸奔模块的测试。

**Architecture:** 批 A(卫生+资源)零行为改动;批 B(边界)以"隔离测试先红后绿"驱动纯移动式迁移,再用 import-linter 锁现状;批 C(类型+覆盖)只加注解与测试、不改运行时逻辑。

**Tech Stack:** Python 3.12 / ruff / mypy / import-linter / pytest(asyncio_mode=auto)/ httpx ASGITransport。

**背景:** codex review 的逐条核实结论见会话记录;其中 telegram→feishu 耦合、typer[all]、覆盖率为上轮重构遗留清单条目(`docs/plan/2026-07-03-refactor-execution-log.md` 遗留 #1/#3/#4)。

## Global Constraints

- **提交流程(用户指定,覆盖各任务的 Commit 步骤)**:全程**不执行 `git commit`**——改动只落工作区/index,任务级 Commit 步骤一律跳过;任务边界由控制者以 `git write-tree` 快照切分审查;全部完成经用户审批后再统一提交。实施者允许的 git 操作仅限:`git rm --cached`(Task 1 untrack)、普通 `mv`(Task 3 用 mv 代替 git mv)、只读命令。
- 测试一律 `.venv/bin/python -m pytest`;每任务收尾 `.venv/bin/ruff check src tests` + `.venv/bin/ruff format src tests` 双净。
- 测试基线 **1399 passed**(`-m "not slow"`)只增不减。
- commit 不加任何 attribution 尾注;消息格式 `<type>: <中文描述>`。
- API 路由零漂移(本轮无新增路由,可用 `scripts/dump_routes.py` 对照)。
- 批 A/B 全程**行为零改动**(纯移动/接线/清理);批 C 修类型**禁止改运行时逻辑**,需行为改动的记 SKIP 清单报告。
- 游离文件(weather.py 等)**只 untrack 不物理删除**(保留本地,非本会话创建的文件不擅删)。

## 明确不做(本轮 out of scope,均有裁决理由)

- loop.py/agent.py 服务化重构——上轮刚做过方法级手术(26 退出点三方核验),等真实痛点;scheduler/profile_synthesizer 拆分列为下轮候选;
- feishu_repo/telegram_repo CRUD 合并——上轮 BotFacade 实证教训(语义差异,抽象收益<复杂度);
- MCP 校验"重复"合并——`_MCPServerCreate` 是 API DTO、`MCPServerConfig` 是 domain 模型,正常分层,不是重复;
- 前端 chunk 拆分与全量 lazy load——内部工具,收益低;
- storage↔secrets、persona↔memory 惰性交叉的"接口注入"改造——先用 import-linter 锁现状(B2),重改造等拆包需求真出现。

---

# 批次 A:卫生与资源生命周期

## Task 1: 仓库卫生与门禁口径统一

根目录游离演示脚本(test_demo.py / weather.py / weather.sh / test_redline/)与 `web/.vite/` 依赖缓存被 git 跟踪,导致 `ruff check .` 63 errors;README 端口与 vite 实配漂移;`typer[all]` extra 在新版已移除。全部为零行为改动。

**Files:**
- Modify: `.gitignore`(追加豁免块)
- Modify: `README.md:79`(端口)
- Modify: `pyproject.toml:30`(typer)
- Untrack: `test_demo.py`、`weather.py`、`weather.sh`、`test_redline/`、`web/.vite/`

**Interfaces:**
- Produces: `ruff check .` 全绿成为可用门禁口径(ruff 默认 respect_gitignore=true,untrack+ignore 后不再扫描)。

- [ ] **Step 1: untrack 游离文件(保留本地)**

```bash
git rm -r --cached test_demo.py weather.py weather.sh test_redline web/.vite
```

- [ ] **Step 2: .gitignore 追加**

```gitignore
# 本地实验/演示脚本(保留本地,不入库)
/test_demo.py
/weather.py
/weather.sh
/test_redline/
# vite 依赖缓存
web/.vite/
```

- [ ] **Step 3: README 端口校准**

`README.md:79` 的 `http://localhost:3000`(两处 3000 字样)改为 `http://localhost:7999`,与 `web/vite.config.ts:7` 一致;该行前后语句连带核对(如"开发时访问 3000"一并改 7999)。

- [ ] **Step 4: typer 依赖声明修正**

`pyproject.toml:30`:`"typer[all]>=0.12",` → `"typer>=0.12",`(新版 typer 已内置原 all 的功能,`[all]` extra 被移除并触发安装警告)。

- [ ] **Step 5: 验证**

```bash
.venv/bin/ruff check .            # 期望: All checks passed!
git ls-files | grep -E "test_demo|weather|test_redline|\.vite"   # 期望: 空
.venv/bin/python -m pytest -q -m "not slow" 2>&1 | tail -1        # 期望: 1399 passed(testpaths=["tests"] 已配,不受根目录文件影响)
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: 仓库卫生——untrack 演示脚本与 vite 缓存,ruff check . 全绿;README 端口/typer 声明校准"
```

## Task 2: DrawerStore 生命周期接线 + tests Storage 泄漏清理

`DrawerStore.close()`(memory/drawer_store.py:283)存在但从未被 shutdown 调用;tests 里 27 个文件直接实例化 `Storage(`,仅 5 处 close,是 sqlite ResourceWarning 的主要来源(也是我们记录的"测试偶发抖动"排查线索之一)。

**Files:**
- Modify: `src/tianshu/bootstrap/wiring_memory.py`(drawer_store 挂 app.state,若未挂)
- Modify: `src/tianshu/app.py`(shutdown 段,`app.state.storage.close()` 之前)
- Modify: tests 下直接实例化 Storage 未关闭的文件(grep 定位,~27 个,逐一补 close)
- Test: `tests/test_app_lifecycle.py`(新建或并入现有 bootstrap 冒烟测试文件,以现状为准)

**Interfaces:**
- Produces: `app.state.drawer_store`(DrawerStore 实例,shutdown 时 close)。

- [ ] **Step 1: 写失败测试(lifespan 退出关闭 drawer_store)**

先 Read `tests/` 下现有 bootstrap/lifespan 冒烟测试(grep `lifespan` tests/),并入该文件;无则新建。close 的调用证据用手动包装采集(mock.patch 的 with 块会提前还原,不适用):

```python
from tianshu.app import create_app, lifespan


async def test_lifespan_closes_drawer_store():
    app = create_app()
    calls: list[int] = []
    async with lifespan(app):
        ds = app.state.drawer_store  # 属性不存在时此处即红
        orig_close = ds.close
        ds.close = lambda: (calls.append(1), orig_close())[-1]
    assert calls, "lifespan 退出应调用 drawer_store.close()"
```

- [ ] **Step 2: 跑测试确认红**

Run: `.venv/bin/python -m pytest tests/test_app_lifecycle.py -q`(路径按 Step 1 实际落点)
Expected: FAIL — `AttributeError: 'State' object has no attribute 'drawer_store'` 或 calls 为空

- [ ] **Step 3: 接线实现**

`wiring_memory.py`:找到 `DrawerStore(` 创建处(约 60 行附近,变量名以文件实际为准),其后补:

```python
    app.state.drawer_store = drawer_store
```

`app.py` shutdown 段,`app.state.storage.close()` **之前**插入:

```python
    app.state.drawer_store.close()
```

- [ ] **Step 4: 验证绿**

Run: `.venv/bin/python -m pytest tests/test_app_lifecycle.py tests/universe/test_universe_api.py -q`
Expected: PASS(后者验证 lifespan 全链路未破)

- [ ] **Step 5: tests Storage 泄漏清理**

```bash
grep -rln "Storage(" tests/ --include="*.py"   # 定位 ~27 个文件
```

逐文件处理(**只补关闭,不改测试逻辑**),两种模式按文件现状择一:
- 已有 fixture 的:fixture 改为 `yield s` 后补 `s.close()`;
- 函数内直接实例化的:包 `try: ... finally: s.close()`,或文件级抽 fixture(同文件多处时)。

清理前后各跑一次统计并记入报告:

```bash
.venv/bin/python -m pytest -q -m "not slow" -W always::ResourceWarning 2>&1 | grep -c "ResourceWarning"
```

验收口径:sqlite 相关 ResourceWarning 显著下降(目标趋零;第三方库自身的告警不计)。

- [ ] **Step 6: 全量验证 + Commit**

Run: `.venv/bin/python -m pytest -q -m "not slow"`(全绿)+ ruff 双净

```bash
git add -A
git commit -m "fix: DrawerStore 接入 shutdown 生命周期 + tests Storage 泄漏清理(ResourceWarning 治理)"
```

---

# 批次 B:边界收紧与契约固化

## Task 3: EdictBridge/PersonaRenderer/SessionAnchor 迁 gateway/core——telegram 去 feishu 依赖

上轮重构遗留 #1。已核实三个待迁模块**零 lark 依赖**(edict_bridge 只 import bus/edict_ops/executor/models/storage/core.errors/session_anchor;persona_renderer 仅 logging+dataclass;session_anchor 20 行仅 Storage)。纯移动式迁移,以隔离测试先红后绿驱动。

**Files:**
- Move: `src/tianshu/gateway/feishu/edict_bridge.py` → `src/tianshu/gateway/core/edict_bridge.py`
- Move: `src/tianshu/gateway/feishu/persona_renderer.py` → `src/tianshu/gateway/core/persona_renderer.py`
- Move: `src/tianshu/gateway/feishu/session_anchor.py` → `src/tianshu/gateway/core/session_anchor.py`
- Modify: 全部引用点 import 更新(已知清单见 Step 3)
- Modify: `src/tianshu/gateway/telegram/approval_commands.py:15`(改直连 core.approval)
- Test: `tests/gateway/test_extras_isolation.py`(新建)

**Interfaces:**
- Produces: `tianshu.gateway.core.edict_bridge.EdictBridge`、`tianshu.gateway.core.persona_renderer.PersonaRenderer`、`tianshu.gateway.core.session_anchor.SessionAnchor`(类名/签名零变);`import tianshu.gateway.telegram` 不再触达 `tianshu.gateway.feishu`。

- [ ] **Step 1: 写隔离测试(当前必红)**

新建 `tests/gateway/test_extras_isolation.py`:

```python
"""extras 独立性:无 lark_oapi 环境下 telegram 入口可导入(子进程黑名单验证)。

conftest 的 lark stub 只在测试进程生效;此处用独立子进程 + MetaPathFinder
黑名单模拟"未安装 lark_oapi"的真实环境。
"""

import subprocess
import sys

_BOOT = """
import sys

class _Block:
    def find_module(self, name, path=None):
        if name == "lark_oapi" or name.startswith("lark_oapi."):
            return self
    def load_module(self, name):
        raise ImportError(f"blacklisted: {name}")

sys.meta_path.insert(0, _Block())
import tianshu.gateway.telegram  # noqa: E402,F401
print("TELEGRAM_OK")
"""


def test_telegram_importable_without_lark():
    proc = subprocess.run(
        [sys.executable, "-c", _BOOT], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}"
    assert "TELEGRAM_OK" in proc.stdout
```

- [ ] **Step 2: 跑测试确认红**

Run: `.venv/bin/python -m pytest tests/gateway/test_extras_isolation.py -q`
Expected: FAIL — 子进程 ImportError(telegram/__init__ 顶层 import feishu.edict_bridge,feishu 包初始化触达 lark)

- [ ] **Step 3: 迁移与 import 更新**

```bash
git mv src/tianshu/gateway/feishu/edict_bridge.py src/tianshu/gateway/core/edict_bridge.py
git mv src/tianshu/gateway/feishu/persona_renderer.py src/tianshu/gateway/core/persona_renderer.py
git mv src/tianshu/gateway/feishu/session_anchor.py src/tianshu/gateway/core/session_anchor.py
```

更新引用(已知代码引用点,以全仓 grep 为准逐一核对):
- `core/edict_bridge.py` 内部:`from tianshu.gateway.feishu.session_anchor import` → `tianshu.gateway.core.session_anchor`;
- `gateway/feishu/__init__.py`、`gateway/telegram/__init__.py`:两处 import 路径 feishu→core;
- `gateway/core/mode_router.py`、`core/edict_branch.py`、`core/assistant_branch.py`:原引用 `feishu.edict_bridge`/`session_anchor` 的改为 core 内部引用(**顺带消除 core→feishu 残余引用**);
- `gateway/telegram/approval_commands.py:15`:`from tianshu.gateway.feishu.approval_commands import` → `from tianshu.gateway.core.approval import`(feishu 侧本就是 re-export shim,telegram 改直连;shim 本身若仍被 feishu 内部消费则保留不动);
- `src/tianshu/storage/` 下的 grep 命中(\_base/schema/feishu_repo/telegram_repo)为 docstring/注释噪声——**甄别,注释里的旧路径顺带校准,代码不动**;
- 全仓终检:`grep -rn "feishu.edict_bridge\|feishu.persona_renderer\|feishu.session_anchor" src/ tests/` 应零命中(docs/ 留给 Step 6)。

- [ ] **Step 4: 验证绿**

Run: `.venv/bin/python -m pytest tests/gateway/test_extras_isolation.py tests/gateway -q && .venv/bin/python -m pytest -q -m "not slow"`
Expected: 隔离测试 PASS;全量 ≥1399+1。若隔离测试仍红,说明 telegram 还有隐藏 feishu 触达链——顺藤继续解(在本任务范围内),stderr 会给出确切 import 链。

- [ ] **Step 5: 文档同步**

`docs/impl/interfaces/README.md` 与 `docs/design/interfaces/gateway.md` 中提及 EdictBridge/PersonaRenderer 位置的行更新为 core(grep `edict_bridge` docs/ 定位现状类文档,历史记录类 docs/plan、docs/superpowers 豁免);上轮遗留清单文档(`docs/plan/2026-07-03-refactor-execution-log.md`)**不改**(历史记录)。

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(gateway): EdictBridge/PersonaRenderer/SessionAnchor 迁 core——telegram 摆脱 feishu 级联依赖(遗留#1)"
```

## Task 4: import-linter 分层契约固化

把现有分层"锁现状防恶化":底层(kernel/models/config/bus)不得向上依赖,中层(storage/secrets/memory/persona/skills)不得依赖编排层。同层横向交叉(storage↔secrets、persona↔memory 的惰性 import)天然放行——layers 契约只禁跨层上引。

**Files:**
- Modify: `pyproject.toml`(dev 依赖 + `[tool.importlinter]` 配置)
- Modify: `README.md`(开发验证命令一行)

**Interfaces:**
- Consumes: Task 3 完成后的 import 图(core 不再引 feishu)。
- Produces: `.venv/bin/lint-imports` 作为常设门禁命令。

- [ ] **Step 1: 安装 dev 依赖**

`pyproject.toml` 的 dev 依赖组(现有 respx 所在组)追加 `"import-linter>=2.0",`,然后:

```bash
.venv/bin/pip install -e ".[dev]" 2>&1 | tail -1   # 或项目现用安装方式,以 dev 组实际名为准
```

- [ ] **Step 2: 写入契约配置**

`pyproject.toml` 追加:

```toml
[tool.importlinter]
root_package = "tianshu"

[[tool.importlinter.contracts]]
name = "分层:底层契约不得向上依赖"
type = "layers"
layers = [
    "tianshu.gateway | tianshu.executor | tianshu.scheduler | tianshu.bootstrap | tianshu.universe",
    "tianshu.storage | tianshu.secrets | tianshu.memory | tianshu.persona | tianshu.skills",
    "tianshu.kernel | tianshu.models | tianshu.config | tianshu.bus",
]
```

- [ ] **Step 3: 迭代豁免清单**

```bash
.venv/bin/lint-imports
```

对每条报告的违规逐一裁决:
- 属于惰性/历史交叉且本轮不改造的 → 加入 `ignore_imports`,**每条附一行 TOML 注释写明理由**(例:`# storage→memory.fts 惰性 import,FTS 建表工具,拆包时再改造`);
- 属于放错层的简单 import(如仅引用常量)→ 优先改代码消除而非豁免(仅限一行级、零行为风险的);
- 无法归类的 → 记入报告,契约收窄(如某包先移出 layers)并说明。

预期豁免量:个位数(中层横向交叉在同层内,不触发 layers 违规;已知的跨层是 storage→memory/secrets 同层、memory→persona 同层,均不违规;真正跨层上引理论上很少)。若豁免超过 15 条,停下报告(BLOCKED with findings)——说明层定义与现实差距大,需要控制者裁决。

- [ ] **Step 4: 验证 + 文档 + Commit**

Run: `.venv/bin/lint-imports`(Contracts: 1 kept)+ 全量测试绿 + ruff 双净
`README.md` 开发/验证章节补一行:`.venv/bin/lint-imports  # 分层契约检查`。

```bash
git add -A
git commit -m "chore: import-linter 分层契约固化——锁现状防跨层回归,豁免带理由"
```

---

# 批次 C:类型与覆盖

## Task 5: mypy 一次扩五包(实测共 29 错)

实测错误量:storage 5 / secrets 1 / memory 14 / persona 6 / tools.mcp 3。量级小,一次扩完。

**Files:**
- Modify: `pyproject.toml`(`[tool.mypy]` packages 列表)
- Modify: 上述五包中带类型错误的 ~9 个源文件(仅注解/窄化)

**Interfaces:**
- Produces: `packages = ["tianshu.models", "tianshu.kernel", "tianshu.bus", "tianshu.dag", "tianshu.config", "tianshu.storage", "tianshu.secrets", "tianshu.memory", "tianshu.persona", "tianshu.tools.mcp"]`

- [ ] **Step 1: 更新 packages 并跑出合并基线**

pyproject `[tool.mypy]` 的 packages 改为上述 10 包列表,然后:

```bash
.venv/bin/python -m mypy 2>&1 | tail -3   # 记录合并跑的确切错误清单(分包跑合计 29,合并跑以实际为准)
```

- [ ] **Step 2: 逐错修复**

修复原则(硬约束):
- 只允许:补类型注解、`X | None` 修正、isinstance/assert 窄化、`dict[str, Any]` 显式化、必要且带理由的 `# type: ignore[code]`;
- **禁止**:改条件分支、改默认值、改异常处理等任何运行时行为;
- 修某个错需要行为改动才能消除时:保留该错为 `# type: ignore[code]  # TODO(治理): <一句原因>` 并记入报告 SKIP 清单。

- [ ] **Step 3: 验证 + Commit**

Run: `.venv/bin/python -m mypy`(Success: no issues)+ `.venv/bin/python -m pytest -q -m "not slow"`(全绿,证明零行为漂移)+ ruff 双净

```bash
git add -A
git commit -m "chore(types): mypy 扩至 storage/secrets/memory/persona/tools.mcp 五包零错(29 处注解修复)"
```

## Task 6: 补测 personas_api + memory_repo

`gateway/personas_api.py` 694 行、`storage/memory_repo.py` 122 行,均无专门测试。API 测试沿用 `tests/universe/test_universe_api.py` 的 client fixture 模式(httpx ASGITransport + 真 lifespan)。**不为凑覆盖写无断言测试**;发现真 bug 停下报告(BLOCKED with findings),不顺手修。

**Files:**
- Test: `tests/gateway/test_personas_api.py`(新建)
- Test: `tests/storage/test_memory_repo.py`(新建;若已有同名文件则并入)

- [ ] **Step 1: personas_api 用例(先 Read personas_api.py 全文,POST/PUT body 字段以源码入参解析为准)**

```python
"""/api/personas 与 /api/departments 端点集成测试。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tianshu.app import create_app, lifespan


@pytest.fixture
async def client():
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_list_departments(client):
    resp = await client.get("/api/departments")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)


async def test_list_personas_contains_builtin(client):
    resp = await client.get("/api/personas")
    assert resp.status_code == 200
    ids = [p["id"] for p in resp.json()["data"]]
    assert "bingbu" in ids  # 内建七部门之一


async def test_get_persona_detail_and_404(client):
    resp = await client.get("/api/personas/bingbu")
    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == "bingbu"
    resp = await client.get("/api/personas/no-such-persona")
    assert resp.status_code == 404


async def test_create_update_delete_persona_roundtrip(client):
    # body 字段以 personas_api.py 的 create_persona 入参解析为准(name/department 等)
    create_body = {...}  # ← 实施时按源码填,断言结构保持
    resp = await client.post("/api/personas", json=create_body)
    assert resp.status_code == 201
    pid = resp.json()["data"]["id"]

    resp = await client.put(f"/api/personas/{pid}", json={...})  # 同上
    assert resp.status_code == 200

    resp = await client.delete(f"/api/personas/{pid}")
    assert resp.status_code == 200
    assert (await client.get(f"/api/personas/{pid}")).status_code == 404


async def test_create_persona_missing_fields_rejected(client):
    resp = await client.post("/api/personas", json={})
    assert resp.status_code in (400, 422)


async def test_persona_templates_list_and_detail(client):
    resp = await client.get("/api/persona-templates")
    assert resp.status_code == 200
    data = resp.json()["data"]
    if data:  # 有模板时抽第一个验证详情
        tid = data[0]["id"]
        assert (await client.get(f"/api/persona-templates/{tid}")).status_code == 200


async def test_persona_metrics_and_profile(client):
    assert (await client.get("/api/personas/bingbu/metrics")).status_code == 200
    resp = await client.get("/api/personas/bingbu/profile")
    assert resp.status_code in (200, 404)  # 无画像时的行为以实现为准,二选一后写死断言
```

(`{...}` 两处必须在实施时以源码为准填入真实字段——这是本任务 Step 1 的第一动作:Read `personas_api.py:195` 起的 create_persona 与 `:385` 起的 update_persona;`profile` 一例先实测再写死断言,不许保留双值断言进 commit。departments 的 POST/PUT/DELETE 同构补 3 条。)

- [ ] **Step 2: memory_repo 用例(entry 构造以 `save_memory_entry` 实际入参与 models 为准)**

```python
"""storage/memory_repo.py 的 CRUD 与检索往返测试(真 :memory: Storage)。"""

import pytest

from tianshu.storage import Storage


@pytest.fixture
def storage():
    s = Storage(db_path=":memory:")
    s.init_db()
    yield s
    s.close()


def test_memory_entry_save_and_list_roundtrip(storage):
    entry = ...  # 以 save_memory_entry 的入参类型为准构造(persona_id/category/content 等)
    storage.save_memory_entry(entry)
    rows = storage.list_memory_by_persona("p-test")
    assert len(rows) == 1
    assert rows[0].content == "测试记忆内容"  # 字段名以返回类型为准


def test_search_memory_hits_and_misses(storage):
    ...  # 存两条不同 content → search_memory 命中其一,关键词不存在时返回空


def test_delete_memory_entry(storage):
    ...  # save → delete_memory_entry(id) 返回 True → list 为空;删不存在的 id 返回 False


def test_delete_memory_entries_batch(storage):
    ...  # 存 3 条 → batch 删 2 条返回 2 → 剩 1 条
```

(四条用例的 `...` 同样是实施首动作补齐——先 Read `memory_repo.py` 五个方法签名与 entry 模型;断言语义按上述注释,不许交空壳。)

- [ ] **Step 3: 验证 + Commit**

Run: `.venv/bin/python -m pytest tests/gateway/test_personas_api.py tests/storage/test_memory_repo.py -q`(全绿,用例数 ≥14)+ 全量绿 + ruff 双净

```bash
git add -A
git commit -m "test: personas_api 与 memory_repo 补测——CRUD 往返/404/校验路径(裸奔清零)"
```

## Task 7: 补测 profile_synthesizer

`persona/profile_synthesizer.py` 710 行零测试。纯函数部分(aggregate/pick/detect)直接测;LLM 部分 fake;`run()` 端到端 fake 全链。

**Files:**
- Test: `tests/persona/test_profile_synthesizer.py`(新建;tests/persona/ 目录不存在则连 `__init__.py` 一起建,对齐相邻目录惯例)

- [ ] **Step 1: 用例(先 Read profile_synthesizer.py 全文;`ProfileSynthesisInput` 构造与 `ProfileSynthesizer.__init__` 依赖以源码为准)**

覆盖面(每条一个测试函数,数据构造实施时按 dataclass 字段填):

```python
"""ProfileSynthesizer:聚合纯函数 / LLM 降级 / 端到端 persist。"""

# 1. test_aggregate_task_distribution —— 造若干 memorial(不同类别/成败),断言分布统计数字精确
# 2. test_aggregate_health —— 成功率/重试的聚合口径断言
# 3. test_pick_degradation_candidates —— 造出"应退化"与"不应退化"两组指标,断言只选中前者
# 4. test_detect_conflict —— prev_markdown 含人工段 + new_auto_section 变化 → True;无人工改动 → False(以该方法真实语义为准,先读实现再写两个方向的断言)
# 5. test_llm_memory_review_bad_json_fails_safe —— fake LLM 返回非 JSON → 返回 [] 或按实现的降级值,不抛异常
# 6. test_run_end_to_end_with_fakes —— fake storage + fake LLM + tmp_path 持久化目录:
#    await synthesizer.run(persona_id=...) → persist 产物文件存在、内容含画像 section;
#    断言 detect_conflict=False 路径正常回写
```

fake LLM 沿用项目惯例(`class _FakeResp: content=...` + `async def chat(...)`,参照 tests/universe/test_diagnostician.py);fake storage 只需提供 collect_inputs 消费的查询方法(读 `collect_inputs` 源码列出清单后逐个打桩)。

- [ ] **Step 2: 红→绿确认(抽 2 条演示)**

新用例先写断言错误值确认能红(如分布统计断言故意 +1),再改正确认绿——报告贴证据。

- [ ] **Step 3: 验证 + Commit**

Run: `.venv/bin/python -m pytest tests/persona -q`(≥6 用例全绿)+ 全量绿 + ruff 双净

```bash
git add -A
git commit -m "test(persona): profile_synthesizer 补测——聚合纯函数/LLM 降级/端到端 persist"
```

---

# 风险与已知边界

1. **Task 2 面广**(~27 个测试文件):只补 close 不改逻辑,若某文件的 Storage 生命周期被测试间共享(module 级 fixture),保持共享语义只在终结处补 close;
2. **Task 3 隔离测试可能揭出隐藏依赖链**:telegram 经其他路径触达 feishu 时,子进程 stderr 给出确切 import 链,顺藤在任务内继续解;若牵出超预期的深层耦合(如需要动 bot_manager),停下报告;
3. **Task 4 豁免爆炸**(>15 条)即 BLOCKED,层定义需控制者裁决,不许静默放宽契约;
4. **Task 5 修类型可能暴露真 bug**(注解揭出 None 传播等):停下报告,不顺手改行为;
5. **Task 6/7 的 `{...}`/`...` 占位**是"输入数据以源码为准"的显式标记,实施首动作即补齐,严禁空壳断言进 commit;
6. 全程无路由/API/行为变化,前端不涉及。

# 收官验证

- [ ] `.venv/bin/ruff check .`(注意是 `.`,新口径)+ `.venv/bin/ruff format --check src tests` 双净;
- [ ] `.venv/bin/python -m mypy` 零错(10 包);
- [ ] `.venv/bin/lint-imports` Contracts kept;
- [ ] `.venv/bin/python -m pytest -q -m "not slow"` 全绿,总数 ≥1399 + 新增用例数;
- [ ] ResourceWarning 前后对比数字(Task 2 报告);
- [ ] 子进程隔离测试常设(telegram 不再依赖 lark);
- [ ] `scripts/dump_routes.py` 路由零漂移。
