# 天枢 G0 事实、术语与迁移安全实施计划

> **执行约束：** 本计划仅实现已批准 Master Roadmap 的 G0。G0 验收完成后停止，不进入 G1。所有行为修改测试先行；不开发或验收手机端；不改 Logo、顶部标语、右上五项、四组十四部门、主题与侧栏折叠能力。

**目标：** 把天枢当前可公开验证的能力、用户术语、桌面审批原型和 SQLite 数据升级路径收敛为一个诚实、可复验、不会在重启时破坏用户数据的开源前基线。

**范围边界：** 保持内部 `approval`、`decree`、数据库字段、事件名和 v1 API 兼容；只迁移用户可见治理语言。当前版本固定为 `0.4.2`，不凭空创建下一版本承诺。现有 Claude Code/Codex CLI 只描述为 `contained + experimental`。原型保持本地、未提交，等待页面验收。

---

## Task 1：冻结“裁决”术语契约

**Files**

- Create: `docs/adr/0012-decision-terminology-not-zhupi.md`
- Create: `web/src/i18n/terminology.test.ts`
- Modify: `web/src/i18n/locales/zh-classic.json`
- Modify: `web/src/i18n/locales/zh-modern.json`
- Modify: `web/src/i18n/locales/en.json`
- Verify only: `CONTEXT.md`

### 1.1 RED：建立可见文案契约

- 递归检查两套中文词典的字符串值，禁止用户可见的 `批红 / 朱批 / 司礼监代批 / 审批 / 待批`。
- 针对治理专用 English key 断言使用 `Decision / Pending Decision / Governed auto-decision`；不全局禁止合法的质量 `review`。
- 断言三套词典 key 结构一致，证明内部兼容 key 未被重命名。

Run:

```bash
cd web && npm test -- --run src/i18n/terminology.test.ts
```

Expected: 因现有旧词值失败。

### 1.2 GREEN：只修改用户可见值

- 将中文治理动作统一为 `裁决`，机器按策略完成的动作统一为 `自动裁决`。
- 英文治理语境统一为 `Decision`；保留 critic/self-review 等质量评审语义。
- ADR 记录 canonical mapping、为何不用“批红”、兼容范围及禁止重命名项。

### 1.3 Verify

```bash
cd web && npm test -- --run src/i18n/terminology.test.ts
rg -n "批红|朱批|司礼监代批|审批|待批" web/src/i18n/locales
```

Expected: test 通过；扫描零命中。

---

## Task 2：修正桌面审批原型的真实性

**Files**

- Modify: `prototypes/tianshu-agent-os/src/App.jsx`
- Modify: `prototypes/tianshu-agent-os/src/data/mockData.js`
- Modify: `prototypes/tianshu-agent-os/src/screens/ControlCenter.jsx`
- Modify: `prototypes/tianshu-agent-os/src/screens/EdictDetail.jsx`
- Modify: `prototypes/tianshu-agent-os/src/screens/EvolutionCenter.jsx`
- Modify: `prototypes/tianshu-agent-os/src/App.test.jsx`
- Modify: `prototypes/tianshu-agent-os/src/styles.css`
- Modify: `prototypes/tianshu-agent-os/design-qa.md`
- Create: `prototypes/tianshu-agent-os/audit-2026-07-11/audit.md`
- Create after browser verification: desktop screenshots under `audit-2026-07-11/`

### 2.1 RED：把已批准页面规则写成测试

- 壳层精确保留现有 Logo、标语、`彩蛋 / 通用 / English / 实时 / 通政`、浅/深色、折叠/展开。
- 导航为 `中枢总览 + 四组十四部门`；生产名称必须为 `百官阁`，不得出现 `百官图`。
- 中枢总览只出现 `待裁决 / 等待裁决 / 查看并裁决`，不得出现旧词、`系统可信`、无口径 `置信度`。
- 高风险批准必须先填写裁决理由，反馈使用 `裁决依据`。
- Canary 为 `18 / 50` 时晋升按钮禁用，并显示样本门禁阻断原因；不得写入“已批准候选晋升”。
- 本任务不新增手机端测试。

Run targeted tests and confirm failures:

```bash
cd prototypes/tianshu-agent-os
npm test -- src/App.test.jsx -t "keeps the production department map"
npm test -- src/App.test.jsx -t "requires a reason for a high-risk decision"
npm test -- src/App.test.jsx -t "blocks promotion until every mandatory gate passes"
```

### 2.2 GREEN：最小修正

- `百官图` 改回生产冻结名称 `百官阁`。
- 全部用户可见旧治理词改为“裁决”。
- `系统可信 98.7%` 改为可解释的 `证据完整率 97 / 100`，带 `过去 24h · 缺 3 项`。
- 技能候选的 `置信度 0.91` 改为 `4 次审计 · 3 次复用成功 · 1 次失败待复盘`。
- 高风险裁决增加理由输入与最小校验。
- 强门不满足时禁用晋升，并提供清晰 disabled 状态；紧急覆盖只作为另行高风险裁决提示，不复用晋升按钮。
- 如现有交互修改了本地状态，提供局部“恢复初始状态”能力；不扩展为生产级撤销协议。

### 2.3 Verify desktop only

```bash
cd prototypes/tianshu-agent-os
npm test -- --no-cache
npm run build
```

- 在 1440×1024 和 1280px 桌面宽度核验三页、深浅色、侧栏展开/折叠、裁决理由、筛选和禁用晋升。
- 新建 `audit-2026-07-11/`，保留 `audit-2026-07-10/` 作为修正前快照。
- 只有壳层与产品语义均通过后才将本次 `design-qa.md` 标为通过。

---

## Task 3：建立公开能力事实矩阵

**Files**

- Create: `docs/launch/capability-matrix.md`
- Create: `tests/test_public_docs_truth.py`
- Modify: `README.md`
- Modify: `README.en.md`
- Modify: `docs/launch/checklist.md`
- Modify: `docs/launch/demo-storyboards.md`
- Modify: `docs/strategy/DECISIONS.md`
- Modify: `web/package-lock.json`

### 3.1 RED：公开承诺与版本一致性测试

- 断言 Python package、Python runtime、FastAPI metadata、Web package 和 Web lock 根版本一致为 `0.4.2`。
- 断言 capability matrix 存在且每项含 `Maturity / Default / Supported scope / Verified guarantee / Explicit non-guarantees / Evidence / Target gate`。
- 断言矩阵明确记录 Keqing：`contained + experimental`、`action_interception=false`、`hard_cost_cap=false`、`pre_run_restore_point=false`。
- 对当前公开入口建立精确禁用承诺清单，防止再次宣称：opaque CLI 逐工具事前治理、真实在线 challenger、自动晋升、真正安全沙箱、每次运行前恢复点或无边界“敢放手”。

Run:

```bash
uv run --frozen pytest -q tests/test_public_docs_truth.py
```

Expected: 因版本漂移、缺矩阵和失真文案失败。

### 3.2 GREEN：按实现证据分级

- `稳定（有限边界）`：可信本地 Native 主链/SQLite 时间线、Native 事前工具策略、本地成本台账/急停/脱敏/clean-env。
- `实验`：Web/IM 裁决、部分 outer-loop checkpoint、Keqing contained adapters、Universe 快照/分支/diff/人工切换、配对评估子进程。
- `规划`：统一远程鉴权、真正容器/OS 沙箱、持久裁决与完整重启恢复、side-effect ledger、Evidence Bundle、真实 challenger 路由、可信自动晋升。
- README 中明确当前仅适合可信本地，不可把无鉴权 API 直接暴露到不可信网络。
- 历史决策保留，但增加“批准/交付不等于 stable；当前事实以矩阵为准”。
- launch checklist 使用 G0–G5，正式宣发只在 G5；G1 只允许 Developer Preview。
- demo storyboard 只保留当前可诚实演示的外围 timeout、clean-env 和最终结果；不伪造飞书按钮、pre-run rollback 或 Keqing 内部逐工具裁决。

### 3.3 Verify

```bash
uv run --frozen pytest -q tests/test_public_docs_truth.py
rg -n -i "full Tianshu governance|每个工具调用|auto-promotion|自动.*晋升|隔离沙箱|safe self-improvement|敢放手" README.md README.en.md docs/launch docs/strategy/DECISIONS.md
git diff --check
```

Expected: tests 通过；扫描仅允许明确否定/规划语境。

---

## Task 4：建立事务化 migration ledger

**Files**

- Create: `src/tianshu/storage/migration_ledger.py`
- Create: `tests/storage/test_migration_ledger.py`

### 4.1 RED：证明事务、顺序和漂移规则

Tests:

- 版本必须为正整数且严格递增；版本与名称唯一。
- 每个 migration 以 `BEGIN IMMEDIATE` 执行，升级内容、完整性检查和 ledger 写入同一事务。
- upgrade 抛错后 DDL、DML 与 ledger 均回滚。
- 已应用 migration 的名称或 checksum 漂移时拒绝启动。
- 已应用版本不重复执行，pending 只返回未执行项。

Run:

```bash
uv run --frozen pytest -q tests/storage/test_migration_ledger.py
```

Expected: import/behavior 失败。

### 4.2 GREEN：最小 ledger API

Implement:

```python
@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    upgrade: Callable[[sqlite3.Connection], None]

def pending_migrations(conn, migrations) -> tuple[Migration, ...]: ...
def apply_migrations(conn, migrations) -> tuple[int, ...]: ...
```

- 表名 `schema_migrations(version, name, checksum, applied_at)`。
- 禁止在 migration callback 内使用会隐式提交的 `executescript()`。
- 完整性检查至少包含 `foreign_key_check` 与 `quick_check`。

---

## Task 5：采用 v0.4.2 基线且不丢数据

**Files**

- Modify: `src/tianshu/storage/schema.py`
- Modify: `src/tianshu/storage/migrations.py`
- Modify: `src/tianshu/storage/_base.py`
- Create: `tests/storage/test_migration_preserves_data.py`
- Modify if required: `tests/test_storage_instance_migration.py`

### 5.1 RED：真实兼容边界

- 程序化构造 pre-ledger v0.4.2 当前 schema，写入并精确比较升级前后的：`edicts`、`memorials`、`supervision_reports`、`cost_ledger`、`personas`、`persona_metrics`。
- 同一数据库重启两次，监督报告和 ledger 不变。
- G0 baseline 只记录一次 `0001_adopt_v042_baseline`。
- 历史 `supervision_reports(edict_id)` 与 `(edict_id, persona_id)` 形态全部映射到每个 edict 最新 memorial（`created_at DESC, id DESC`）。
- 任一旧报告无法映射时 fail closed，旧表/旧行保持不变，ledger 不记版本。
- 历史 session tables 的 `instance_id` 升级继续通过，且不再位于 ledger 外。

### 5.2 GREEN：收敛为当前完整 schema + 安全基线

- 使 `schema.py` 表示完整 v0.4.2 最终 schema，fresh install 不依赖重复运行旧的补丁 SQL。
- 定义唯一 G0 baseline `0001_adopt_v042_baseline`；G0 明确只承诺经测试的 v0.4.2 pre-ledger → ledger 基线。
- 当前 `(memorial_id, persona_id)` 监督表直接 no-op。
- 旧表迁移使用临时表、稳定映射、逐行/数量/关键 payload 校验；校验通过前绝不 drop 原表；禁止 `INSERT OR IGNORE` 掩盖冲突。
- 遗留临时表存在时 fail closed。
- 删除基于错误字符串吞掉 `OperationalError` 和逐语句 commit 的旧迁移路径。

### 5.3 Verify

```bash
uv run --frozen pytest -q \
  tests/storage/test_migration_ledger.py \
  tests/storage/test_migration_preserves_data.py \
  tests/test_storage_instance_migration.py \
  tests/test_storage.py \
  tests/test_supervision.py
```

---

## Task 6：在线备份、离线安全恢复与启动接线

**Files**

- Create: `src/tianshu/storage/sqlite_backup.py`
- Modify: `src/tianshu/storage/_base.py`
- Modify: `src/tianshu/cli/commands/secrets.py`
- Create: `tests/storage/test_backup_restore.py`

### 6.1 RED：覆盖 WAL 与原子恢复

- 在线备份包含已提交但尚未 checkpoint 的 WAL 数据。
- backup → mutate → offline restore 往返恢复原数据。
- 损坏备份拒绝恢复，目标库保持不变。
- 升级失败前生成的自动备份可恢复。
- 没有 pending migration 或使用 `:memory:` 时不产生备份。

### 6.2 GREEN：最小安全实现

- 使用 `sqlite3.Connection.backup()`，不再对打开中的数据库使用 `shutil.copy()`。
- 同目录临时文件写入，`quick_check` 成功后 `os.replace()`，权限 `0600`。
- restore 仅为离线 API；替换前验证备份，成功后清理旧 `-wal/-shm`。
- Storage 启动时：连接 → 检测 pending → 已存在磁盘库则先备份 → 事务迁移 → integrity check → seed/FTS。
- 备份失败必须发生在任何 schema 修改前并终止；迁移失败关闭连接并报告 migration 与备份路径，不自动覆盖原库。
- secrets backup 命令复用相同在线备份 API。

### 6.3 Verify

```bash
uv run --frozen pytest -q \
  tests/storage/test_backup_restore.py \
  tests/storage/test_migration_preserves_data.py
```

---

## Task 7：G0 全量验收与页面证据

### 7.1 Focused quality gates

```bash
uv run --frozen ruff check \
  src/tianshu/storage src/tianshu/cli/commands/secrets.py \
  tests/storage tests/test_public_docs_truth.py
uv run --frozen ruff format --check \
  src/tianshu/storage src/tianshu/cli/commands/secrets.py \
  tests/storage tests/test_public_docs_truth.py
uv run --frozen mypy
uv run --frozen lint-imports
```

### 7.2 Full regression

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen lint-imports
uv run --frozen pytest -m "not slow" -q

cd web
npm run lint
npm run typecheck
npm test -- --run
npm run build

cd ../prototypes/tianshu-agent-os
npm test -- --no-cache
npm run build
```

### 7.3 Static and repository integrity

```bash
rg -n "批红|朱批|司礼监代批|审批|待批|百官图|系统可信|置信度" \
  web/src/i18n/locales prototypes/tianshu-agent-os/src
git diff --check
git diff --exit-code -- uv.lock
```

### 7.4 Independent reviews

- 规格审查：逐条核对 G0 acceptance，拒绝把规划能力写成已实现。
- 代码质量审查：重点检查 migration 原子性、备份先于写入、旧表数据映射、测试是否真正覆盖丢数复现。
- 浏览器审查：仅桌面 1440×1024 与 1280px，深/浅色、展开/折叠、三页关键状态。

### 7.5 Handoff

输出：

- G0 改动摘要；
- 新鲜测试数量、警告、构建体积和耗时；
- migration/backup 恢复演练证据；
- 三页桌面截图与 QA 结论；
- 仍属实验/规划的风险清单；
- 是否满足 G0、是否建议开启 G1。

然后停止，等待用户验收；不得自行进入 G1、提交、推送或发布。
