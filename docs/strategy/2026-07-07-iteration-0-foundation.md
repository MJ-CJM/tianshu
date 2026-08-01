# 迭代 0「地基」实施计划(v0.2.0 + soft launch)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一周内还清开源前置工程债——CI 锁住质量门禁、补齐仓库卫生文件、修复已确认缺陷、前端质量线起步、feat_phase8 合回 main 并打 v0.2.0(soft launch:仓库公开但不宣传)。

**Architecture:** 不动业务代码结构;新增 `.github/workflows/ci.yml`(后端/前端双 job)、仓库根卫生文件、`web/` 的 eslint+vitest 配置;唯一的源码改动是 `profile_synthesizer.py` 的 gather 结果窄化修复(抽一个可单测的模块级函数)。

**Tech Stack:** GitHub Actions + uv(仓库已有 `uv.lock`)/ pre-commit + ruff-pre-commit / eslint 9(flat config)+ typescript-eslint / vitest(node 环境,首批只测纯函数,不引入 jsdom)。

**Spec:** 2026-07-07 发展战略与迭代排期 spec §七 迭代 0 行（内部经营文档，未随仓库公开）。

## Global Constraints

- Python ≥ 3.12;Node ≥ 20;包管理用 uv(锁文件已存在),CI 安装口径 `uv sync --extra all --extra dev`(**不含** `scrapling`——它不在 `all` 聚合 extra 内,本地 1422 测试即在此依赖集下通过)
- 质量门禁五件套(与本地一致,全部必须绿):`ruff check .`、`ruff format --check .`、`mypy`、`lint-imports`、`pytest -m "not slow"`
- mypy 配置含 `warn_unused_ignores=True` —— 修复代码后**必须删除**相应 `# type: ignore`,否则 mypy 红
- 提交信息用 conventional commits(feat/fix/docs/test/chore/ci);Attribution 已全局禁用,不加 Co-Authored-By
- 所有工作在 `feat_phase8` 分支上完成,最后经 PR 合入 main(Task 6)
- 跑 Python 一律用 `.venv/bin/python` / `.venv/bin/pytest`(裸 python 指向错误环境)

---

### Task 1: profile_synthesizer CancelledError 修复(TDD)

**Files:**
- Modify: `src/tianshu/persona/profile_synthesizer.py:544-558`(gather 结果窄化)+ `:576-582`(移除 type: ignore 与 TODO)
- Test: `tests/persona/test_profile_synthesizer.py`(追加 3 个测试)

**Interfaces:**
- Produces: 模块级函数 `_narrow_list_result(value: object, label: str) -> list`(测试直接 import 它;仅本模块内使用,下划线私有)
- 背景:`asyncio.gather(..., return_exceptions=True)` 会把 `CancelledError`(BaseException)也作为结果返回;现有 `isinstance(x, Exception)` 判定漏掉它,后续 `_format_specialties(specialties)` 会拿到异常对象炸 TypeError。修法=正向判定 `isinstance(x, list)`(代码内 `TODO(治理)` 已注明此方案)。

- [ ] **Step 1: 写失败测试**

在 `tests/persona/test_profile_synthesizer.py` 文件末尾追加:

```python
class TestNarrowListResult:
    """gather(return_exceptions=True) 结果窄化 —— CancelledError(BaseException)回归锚点。"""

    def test_list_passes_through(self):
        from tianshu.persona.profile_synthesizer import _narrow_list_result

        assert _narrow_list_result([{"name": "x"}], "llm_specialties") == [{"name": "x"}]

    def test_exception_downgrades_to_empty(self):
        from tianshu.persona.profile_synthesizer import _narrow_list_result

        assert _narrow_list_result(ValueError("boom"), "llm_specialties") == []

    def test_cancelled_error_downgrades_to_empty(self):
        # CancelledError 继承 BaseException 而非 Exception,
        # 旧代码 isinstance(x, Exception) 漏判 —— 本缺陷的直接回归用例
        import asyncio

        from tianshu.persona.profile_synthesizer import _narrow_list_result

        assert _narrow_list_result(asyncio.CancelledError(), "llm_specialties") == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/persona/test_profile_synthesizer.py::TestNarrowListResult -v`
Expected: 3 个 FAIL,报 `ImportError: cannot import name '_narrow_list_result'`

- [ ] **Step 3: 实现窄化函数并替换调用点**

在 `src/tianshu/persona/profile_synthesizer.py` 模块级(`logger = logging.getLogger(__name__)` 之后)加:

```python
def _narrow_list_result(value: object, label: str) -> list:
    """gather(return_exceptions=True) 结果正向窄化:非 list 一律降级为空列表并告警。

    正向判定覆盖 Exception 与 BaseException(如 CancelledError)——
    后者用 isinstance(x, Exception) 会漏判,导致异常对象流入后续格式化。
    """
    if isinstance(value, list):
        return value
    logger.warning("%s raised or returned non-list: %r", label, value)
    return []
```

把 544-557 行的判定块(两个 `isinstance(x, Exception)` 分支 + `review_raw` 行 + 整段 `TODO(治理)` 注释)替换为:

```python
            specialties = _narrow_list_result(specialties, "llm_specialties")
            degradations = _narrow_list_result(degradations, "llm_degradations")
            review_items: list[dict[str, str]] = _narrow_list_result(review_raw, "llm_memory_review")

            degraded = self._is_degraded(inputs, specialties, degradations)
```

(558 行原有的 `# type: ignore[arg-type]` 一并删除。)

再把 575-583 行的 `ProfileSections(...)` 中两处 `# type: ignore[arg-type]` 及其上方 3 行 TODO 注释删除,恢复为干净调用:

```python
            sections = ProfileSections(
                specialties_md=_format_specialties(specialties),
                task_distribution_md=_format_task_distribution(task_dist),
                health_md=_format_health(health),
                degradations_md=_format_degradations(candidates, degradations),
            )
```

- [ ] **Step 4: 跑测试与门禁确认通过**

Run: `.venv/bin/pytest tests/persona/test_profile_synthesizer.py -v`
Expected: 全部 PASS(新增 3 个 + 既有用例不回归)

Run: `.venv/bin/mypy && .venv/bin/ruff check src/tianshu/persona/profile_synthesizer.py`
Expected: mypy `Success: no issues found`(ignore 已删,无 unused-ignore 告警);ruff 通过

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/persona/profile_synthesizer.py tests/persona/test_profile_synthesizer.py
git commit -m "fix(persona): gather 结果正向窄化——CancelledError(BaseException)漏判修复"
```

---

### Task 2: 测试抖动定位与处置(调查型,timebox 半天)

**Files:**
- 视定位结果 Modify:`tests/gateway/` 下 feishu webhook 相关测试 / `tests/universe/` 下 switch 相关测试
- 兜底 Modify: `pyproject.toml`(注册 `flaky` marker)

**Interfaces:**
- 背景:coverage 跑批偶发个位数失败,已知嫌疑区=feishu webhook 与 universe switch 测试,疑 hash-seed 依赖或 async 资源泄漏(sqlite ResourceWarning 源已在 5d197ef 消除)
- Produces: 要么修复(首选),要么隔离——`@pytest.mark.flaky` 标记 + CI 排除 + CONTRIBUTING 记录待办。**禁止 xfail 掩盖**

- [ ] **Step 1: 定位嫌疑测试文件**

```bash
grep -rl "webhook" tests/gateway/ --include="*.py"
grep -rl "switch" tests/universe/ --include="*.py"
```

记下输出的文件清单(下步复现用)。

- [ ] **Step 2: 定向复现(固定种子 vs 随机种子对照)**

```bash
# 随机 hash seed 跑 15 轮(替换 <FILES> 为上步清单)
for i in $(seq 1 15); do .venv/bin/pytest <FILES> -q --tb=line -p no:cacheprovider 2>&1 | tail -1; done
# 固定 hash seed 跑 15 轮对照
for i in $(seq 1 15); do PYTHONHASHSEED=0 .venv/bin/pytest <FILES> -q --tb=line -p no:cacheprovider 2>&1 | tail -1; done
```

Expected: 若随机 seed 下偶发失败而固定 seed 全绿 → hash 顺序依赖(如依赖 dict/set 迭代序的断言);若两者都偶发 → async 泄漏/时序竞争,用 `-W error::ResourceWarning` 再跑一轮收集警告。

- [ ] **Step 3: 修复或隔离**

修复优先:hash 顺序依赖 → 断言改为排序后比较(`sorted(...)`)或集合比较;时序竞争 → 补 `await`/事件同步点。修完重复 Step 2 验证 15 轮全绿。

半天 timebox 用尽仍无法根修的:在 `pyproject.toml` 的 `markers` 列表追加一行:

```toml
    "flaky: 已知偶发抖动待根修(CI 暂排除,见 CONTRIBUTING)",
```

给嫌疑测试函数加 `@pytest.mark.flaky`,并验证 `.venv/bin/pytest -m "not slow and not flaky" -q` 稳定 15 轮全绿(此时 Task 5 的 CI pytest 命令相应用 `-m "not slow and not flaky"`)。

- [ ] **Step 4: 全量回归**

Run: `.venv/bin/pytest -m "not slow" -q`(若走了隔离路线则 `-m "not slow and not flaky"`)
Expected: 1425 passed(1422 + Task 1 新增 3)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "test: 定位并处置 feishu webhook/universe switch 偶发抖动"
```

---

### Task 3: 仓库卫生四件套(LICENSE / CHANGELOG / CONTRIBUTING / pre-commit)

**Files:**
- Create: `LICENSE`、`CHANGELOG.md`、`CONTRIBUTING.md`、`.pre-commit-config.yaml`
- Modify: `pyproject.toml`(dev extras 加 `pre-commit`)

**Interfaces:**
- Produces: `npm/uv 无关`的纯文档 + pre-commit 钩子(只挂 ruff 两个 hook,防止对全仓文件的意外重写);消除 README MIT 徽章与仓库无 LICENSE 的不一致

- [ ] **Step 1: 写 LICENSE(MIT 标准文本)**

创建 `LICENSE`:

```text
MIT License

Copyright (c) 2026 mj-cjm

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: 写 CHANGELOG.md**

创建 `CHANGELOG.md`(Keep a Changelog 风格,首条即 0.2.0):

```markdown
# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

## [0.2.0] - 2026-07(soft launch)

首个对外版本。核心能力:

### Added
- **诏令全链路**:Edict → Scheduler → Planner → Agent/DAG → Auditor → Notifier,事件主链路全程落库为可复盘时间线
- **Agent 核心**:ReAct 循环、10 种 ExitReason、不可变 LoopState、三层上下文压缩、流式输出、Anthropic prompt cache
- **六部官制**:多 Persona 权限矩阵、部门智能路由、8 层 PromptBuilder、朝廷(court)共享记忆
- **记忆宫殿**:Markdown 真相源 + SQLite FTS5 全文检索 + Drawer 快照 + 人格成长画像合成
- **长任务外环**:AcceptanceCriteria 验收契约 + bash/lint/rubric 客观检查 + 多监督官 critic + L0–L3 分级升级(会诊/人工)
- **治理**:工具分级(tier)+ 策略管线 + 人工批红(Decree)+ 规则/LLM 双层审计 + 会话规则
- **成本治理**:token 计量、预算熔断、按模型/任务/官员多维归因
- **平行位面自进化**:行为 + 代码双层位面,配对沙箱评估、五维 fitness、自动晋升与自重部署回滚、太医诊断器
- **多入口**:Web / HTTP API / CLI / 飞书 / Telegram;MCP 客户端;鸿胪寺外网治理(SSRF/白名单/凭证托管/限流)
- **工程**:CI 质量门禁(ruff/mypy/import-linter/pytest/前端 tsc)、pre-commit、mypy 十包零错、import-linter 分层契约
```

- [ ] **Step 3: 写 CONTRIBUTING.md**

创建 `CONTRIBUTING.md`:

```markdown
# 贡献指南

## 开发环境

- Python ≥ 3.12,Node ≥ 20
- 后端:`uv sync --extra all --extra dev`(或 `pip install -e ".[all,dev]"`)
- 前端:`cd web && npm install`
- 启用钩子:`uv run pre-commit install`

## 质量门禁(提交前本地全绿)

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/lint-imports          # 分层架构契约
.venv/bin/pytest -m "not slow" -q
cd web && npm run lint && npm run typecheck && npm test -- --run
```

## 分支与提交

- `main` 常绿;特性走短分支(`feat/<topic>`,1–3 周内经 PR 合入)
- 提交信息用 conventional commits:`feat:` `fix:` `refactor:` `docs:` `test:` `chore:` `perf:` `ci:`
- 新增代码测试覆盖尽量 ≥ 90%;仓库整体覆盖率目标 80%(现状 63%,每迭代递增)

## 测试

- 慢测试(真实子进程)标记 `@pytest.mark.slow`,CI 默认排除
- 偶发抖动测试标记 `@pytest.mark.flaky` 并附根修 TODO,禁止 xfail 掩盖

## 贡献策略(首发期,详见 docs/adr/0005)

- 欢迎 issue、讨论、文档改进与小修 PR(typo/bugfix)
- **特性 PR 请先开 issue 对齐**——项目处于滚动排期高速迭代期,未经对齐的大 PR 可能被建议拆分或归档
- issue 尽力 48 小时内响应;单人维护,不作 SLA 承诺
- 行为准则采用 [Contributor Covenant](https://www.contributor-covenant.org/zh-cn/version/2/1/code_of_conduct/)
```

(若 Task 2 走了隔离路线,门禁命令相应写 `-m "not slow and not flaky"`。)

- [ ] **Step 4: 配 pre-commit(仅 ruff 两个 hook,并装依赖)**

`pyproject.toml` 的 dev extras 追加一行(`"import-linter>=2.0",` 之后):

```toml
    "pre-commit>=3.7",
```

创建 `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.5.7 # 占位,下一步 autoupdate 到最新
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

```bash
uv sync --extra all --extra dev
uv run pre-commit autoupdate
uv run pre-commit run --all-files
```

Expected: autoupdate 把 rev 提到当前最新;`run --all-files` 两个 hook 全部 `Passed`(仓库 ruff 本已全绿,若 autoupdate 后的新版 ruff 引入新告警,按告警最小修复)。

- [ ] **Step 5: Commit**

```bash
git add LICENSE CHANGELOG.md CONTRIBUTING.md .pre-commit-config.yaml pyproject.toml uv.lock
git commit -m "chore: 开源卫生四件套——MIT LICENSE/CHANGELOG/CONTRIBUTING/pre-commit"
```

---

### Task 4: 前端质量线(eslint 9 + vitest 起步)

**Files:**
- Create: `web/eslint.config.js`、`web/src/utils/format.test.ts`
- Modify: `web/package.json`(devDependencies + scripts `lint`/`test`)

**Interfaces:**
- Produces: `npm run lint`(eslint flat config)与 `npm test -- --run`(vitest)两个脚本——Task 5 的 CI 前端 job 按这两个名字调用
- 首批测试只测纯函数(`format.ts`),node 环境零浏览器依赖;组件测试(jsdom/testing-library)留迭代 5

- [ ] **Step 1: 装依赖并加脚本**

```bash
cd web
npm install -D eslint @eslint/js globals typescript-eslint \
  eslint-plugin-react-hooks eslint-plugin-react-refresh vitest
```

`web/package.json` 的 `scripts` 追加两行:

```json
    "lint": "eslint .",
    "test": "vitest"
```

- [ ] **Step 2: 写 eslint flat config**

创建 `web/eslint.config.js`(vite react-ts 标准配置):

```js
import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: { ecmaVersion: 2020, globals: globals.browser },
    plugins: { "react-hooks": reactHooks, "react-refresh": reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
    },
  },
);
```

- [ ] **Step 3: 首跑 lint 并收敛到零 error**

Run: `npm run lint`
Expected: 存量 131 个文件首跑可能有告警。处置原则:**零 error 为出口**——个别规则若错误量大且属风格类(典型如 `@typescript-eslint/no-explicit-any`),在 config 的 `rules` 里降为 `"warn"` 并在 CONTRIBUTING.md 的"质量门禁"节下追加一行记录(`<!-- 迭代 5 前端质量线收紧:no-explicit-any warn→error -->`);真实缺陷类 error(未使用变量、hooks 依赖错误)逐个修复。

- [ ] **Step 4: 写 vitest 首批测试(纯函数,真实断言)**

创建 `web/src/utils/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formatDuration, formatTokens, truncateId } from "./format";

describe("formatTokens", () => {
  it("小于 1K 原样输出", () => {
    expect(formatTokens(999)).toBe("999");
  });
  it("K 级保留一位小数", () => {
    expect(formatTokens(1500)).toBe("1.5K");
  });
  it("M 级保留一位小数", () => {
    expect(formatTokens(2_500_000)).toBe("2.5M");
  });
});

describe("truncateId", () => {
  it("超长截断加省略号", () => {
    expect(truncateId("abcdefghij")).toBe("abcdefgh…");
  });
  it("不超长原样返回", () => {
    expect(truncateId("abc")).toBe("abc");
  });
});

describe("formatDuration", () => {
  it("分秒组合", () => {
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T00:01:30Z")).toBe("1m 30s");
  });
  it("一分钟内只显示秒", () => {
    expect(formatDuration("2026-01-01T00:00:00Z", "2026-01-01T00:00:45Z")).toBe("45s");
  });
  it("缺起止返回占位符", () => {
    expect(formatDuration(null, "2026-01-01T00:00:45Z")).toBe("—");
  });
  it("负时长返回占位符", () => {
    expect(formatDuration("2026-01-01T00:01:00Z", "2026-01-01T00:00:00Z")).toBe("—");
  });
});
```

- [ ] **Step 5: 跑测试与既有门禁确认通过**

Run: `npm test -- --run`
Expected: 9 passed(vitest 自动发现 `*.test.ts`,node 环境无需额外配置)

Run: `npm run typecheck && npm run build`
Expected: 均通过(确认新增 devDeps/测试文件不破坏 `tsc -b`;若 `tsc -b` 把 `format.test.ts` 计入且报 vitest 类型缺失,在 `web/tsconfig.app.json` 的 `exclude` 加 `"src/**/*.test.ts"`)

- [ ] **Step 6: Commit**

```bash
git add web/package.json web/package-lock.json web/eslint.config.js web/src/utils/format.test.ts
# 若 Step 3 改了 CONTRIBUTING.md 或 tsconfig 一并 add
git commit -m "chore(web): 前端质量线起步——eslint 9 flat config + vitest 纯函数首测"
```

---

### Task 5: CI workflow(后端五件套 + 前端四连)

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Task 4 的 `npm run lint` / `npm test`;Task 2 决定的 pytest marker 口径(`not slow` 或 `not slow and not flaky`)
- Produces: push main 与所有 PR 上的必过检查(Task 6 的合并前提)

- [ ] **Step 1: 写 workflow**

创建 `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv python install 3.12
      - run: uv sync --extra all --extra dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy
      - run: uv run lint-imports
      - run: uv run pytest -m "not slow" -q

  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: web
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
          cache-dependency-path: web/package-lock.json
      - run: npm ci
      - run: npm run lint
      - run: npm run typecheck
      - run: npm test -- --run
      - run: npm run build
```

(若 Task 2 引入了 flaky marker,pytest 行改为 `uv run pytest -m "not slow and not flaky" -q`。)

- [ ] **Step 2: 本地预演——排除 .env 与本机 venv 的隐性依赖**

CI 环境没有 `.env`,先在本地验证测试不依赖它:

```bash
mv .env .env.bak
uv sync --extra all --extra dev
uv run pytest -m "not slow" -q --tb=line 2>&1 | tail -3
mv .env.bak .env
```

Expected: 与 Task 2 出口一致的全绿。若挪走 `.env` 后出现失败,逐个修复测试对环境变量的隐性依赖(测试内显式 `monkeypatch.setenv`),**不得**在 CI 里塞真实密钥。

- [ ] **Step 3: 推分支开 PR 触发首跑**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: GitHub Actions 质量门禁——后端五件套 + 前端 lint/typecheck/test/build"
git push -u origin feat_phase8
gh pr create --base main --title "迭代 0「地基」:CI/卫生四件套/CancelledError 修复/前端质量线" \
  --body "$(cat <<'EOF'
## Summary
- fix(persona): gather 结果正向窄化,CancelledError 漏判修复(+3 回归测试)
- test: feishu webhook/universe switch 抖动定位与处置
- chore: LICENSE(MIT)/CHANGELOG/CONTRIBUTING/pre-commit
- chore(web): eslint 9 + vitest 起步
- ci: 后端五件套 + 前端四连双 job

## Test plan
- [ ] CI backend job 绿(ruff/format/mypy/lint-imports/pytest)
- [ ] CI frontend job 绿(lint/typecheck/vitest/build)
- [ ] 本地无 .env 预演通过

对应计划:docs/strategy/2026-07-07-iteration-0-foundation.md(spec §八 迭代 0)
EOF
)"
```

- [ ] **Step 4: 看首跑结果并修红**

Run: `gh pr checks --watch`
Expected: backend/frontend 两 job 全绿。常见首跑红与处置:缺依赖(某测试 import 了 all 之外的 extra)→ 给该测试加 `pytest.importorskip("<pkg>")` 或在 sync 命令补 `--extra <name>`;网络依赖测试 → 标记 `slow` 排除。每次修复后 push,直到全绿。

- [ ] **Step 5: (无独立 commit)**

本 task 的 commit 已在 Step 3 完成;修红的追加 commit 用 `ci:` 前缀。

---

### Task 6: 合入 main + v0.2.0 tag

**Files:**
- 无源码改动(纯 git 操作)

**Interfaces:**
- Consumes: Task 5 的 PR 全绿
- Produces: main HEAD = 迭代 0 全部内容;annotated tag `v0.2.0`;远端 main 与 tag 就绪(soft launch 前提)

- [ ] **Step 1: 确认 PR 绿后合并(保留 merge commit,延续 main 惯例)**

```bash
gh pr checks   # 确认全部 pass
gh pr merge --merge --subject "Merge feat_phase8 into main — 迭代 0「地基」收官"
```

Expected: 合并成功(main 是 feat_phase8 祖先,已验证无分叉,不会有冲突)。

- [ ] **Step 2: 本地同步并打 tag**

```bash
git checkout main && git pull origin main
git tag -a v0.2.0 -m "v0.2.0 — soft launch: 诏令全链路/六部官制/记忆宫殿/长任务外环/平行位面自进化/多入口;CI 质量门禁上线(详见 CHANGELOG.md)"
git push origin v0.2.0
```

- [ ] **Step 3: 验证 main 上 CI 绿**

Run: `gh run list --branch main --limit 1`
Expected: 最新 run `completed success`(on: push: branches: [main] 触发)。

- [ ] **Step 4: 回到工作分支**

```bash
git checkout feat_phase8 && git merge main   # 快进对齐,后续特性从 main 开新短分支
```

---

### Task 7: soft launch 收尾清单(人工操作 + 核对)

**Files:**
- 无代码;GitHub 仓库设置操作

**Interfaces:**
- Consumes: Task 6 的 main + v0.2.0 就绪
- Produces: 仓库公开(不宣传);迭代 0 出口达成

- [ ] **Step 1: 公开前安全终检**

```bash
git log --all --diff-filter=A --name-only -- "*.env" "*.pem" "*.key" | head
grep -rn "sk-[A-Za-z0-9]\{20\}\|api_key.*=.*['\"][A-Za-z0-9]\{20\}" src/ web/src/ docs/ --include="*.py" --include="*.ts" --include="*.md" | grep -v "example\|placeholder\|TIANSHU_" | head
```

Expected: 两条均无输出(历史无误提交的密钥文件、源码/文档无硬编码密钥)。若有任何命中:**停止公开**,先轮换该密钥并评估是否需要历史改写。

- [ ] **Step 2: GitHub 仓库设置(人工,在网页完成)**

- Settings → General → Danger Zone → Change visibility → **Public**
- About:描述填 "天枢 Tianshu — 异步、可治理、会成长的 AI 执行平台 / An async, governable, self-evolving AI execution platform";Topics 加 `ai-agent` `agent-platform` `llm` `fastapi` `self-evolving`
- Settings → Branches → main 加 branch protection:Require status checks(backend, frontend)

- [ ] **Step 3: 出口核对(迭代 0 完成定义)**

- [ ] main HEAD 日期为本周,CI 徽章绿
- [ ] LICENSE 存在,README MIT 徽章不再失实
- [ ] `.venv/bin/pytest -m "not slow" -q` 全绿(含 CancelledError 回归测试)
- [ ] `cd web && npm run lint && npm test -- --run` 全绿
- [ ] 仓库 Public,v0.2.0 tag 可见
- [ ] **不做任何宣传动作**(正式宣发在迭代 3.5 后,见 spec §七宣发叙事与 §八排期)

---

## Self-Review 记录

- **Spec 覆盖**:spec §八迭代 0 行的六项——CI 五件套+前端质量线(Task 4/5)、LICENSE/CHANGELOG/CONTRIBUTING/pre-commit(Task 3)、合 main(Task 6)、CancelledError 修复(Task 1)、抖动定位(Task 2)、eslint+vitest 起步(Task 4)——全部有对应 task;soft launch 出口由 Task 7 承接。✓
- **占位符扫描**:pre-commit 的 `rev: v0.5.7` 是显式声明的占位,同一步骤内以 `pre-commit autoupdate` 消解,非悬空 TBD。其余步骤均为完整代码/命令。✓
- **类型/命名一致性**:`_narrow_list_result` 在 Task 1 的测试与实现中签名一致;`npm run lint`/`npm test` 脚本名与 Task 5 CI 调用一致;pytest marker 口径在 Task 2→3→5 三处联动(均已标注条件分支)。✓
