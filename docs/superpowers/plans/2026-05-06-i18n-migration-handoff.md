# Tianshu Web i18n 迁移 — Session Handoff

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to continue this multi-batch migration.

**用途：** 接续本 session 未完成的 i18n 迁移工作。下次 session 开始时，user 说 "按 docs/superpowers/plans/2026-05-06-i18n-migration-handoff.md 继续 i18n 迁移" 即可一键续作。

---

## 已完成进度（已 merge 到 feat_phase6 分支）

| 批次 | Commit | 范围 |
|------|--------|------|
| 1 | `ec6ed02` | i18n 基建 + 御书房闭环（useLocale/useT/三套JSON/LocaleSwitcher/AppHeader/AppSidebar/constants/edictPhase/DecreeModal/ApprovalQueuePage） |
| 2 | `d60f934` | 事件流 + 列表（EventTimeline/MemorialCard/PendingToolCallCard/EdictListPage） |
| 3a | `e7d5e43` | 敕令详情页 + 创建页（EdictDetailPage/EdictCreatePage） |

**已可三档切换的页面**：侧边栏 / 御书房（审批中心）/ 任务详情页 / 任务创建页 / 任务列表页 / 朱批弹窗 / 事件时间线 / 奏折卡片 / 待审批工具卡片。

---

## 剩余批次

### 🔴 批次 3b：EdictForm.tsx（856 行，最大单文件）

**File:** `web/src/components/edict/EdictForm.tsx`

**估算 keys：** ~130

**已在 i18n JSON 中预留好的 keys**（在 zh-classic.json / zh-modern.json / en.json 三套已就位）：
- `form.edict.field.*`（30+ 字段 label）
- `form.edict.placeholder.*`（20+ 占位文）
- `form.edict.validation.*`（5 个验证消息）
- `form.edict.tooltip.*`（13 个长 tooltip）
- `form.edict.option.*`（5 个选项）
- `form.edict.section.*`（4 个区块标题）
- `form.edict.template.*`（4 个模板按钮）
- `form.edict.warning.*`（critic self-review 警告）
- `form.check.*`（check 子表单完整集）
- `priority.*` / `reviewPolicy.*` / `scheduleType.*` / `executionProfile.*` / `strictness.*` / `exhaustion.*` / `criticUnavail.*` / `checkKind.*`

**唯一需做的事：** import `useT` + 把 EdictForm 中的中文字面量替换为 `t("...")`。**JSON 已就位，不用改 JSON。**

**关键陷阱：**
- 内部 `Form.List` 的 `render` 函数有 `(t: string) => ...` 这种局部变量名，会 shadow 外层 `const t = useT()` —— 把局部变量改名为 `tool` / `item` 之类避免误用。
- `DEPT_LABEL` 字典（line 46-53）改用 `t(\`dept.${department}\`)`，无需保留字典。

---

### 🟡 批次 4：12 个部门 Dashboard（~600 keys）

按用户高频访问顺序：

| 文件 | 行数（粗估） | 优先级 |
|------|--------------|--------|
| `pages/SessionRulesPage.tsx` | 中 | 高（已部分改） |
| `pages/SystemManagementPage.tsx` | 大（~176 条） | 高 |
| `pages/AuditDashboardPage.tsx` | 中 | 中 |
| `pages/SchedulerPage.tsx` | 中 | 中 |
| `pages/MemoryDashboardPage.tsx` | 中（~65 条） | 中 |
| `pages/PersonaDashboardPage.tsx` | 中（~111 条） | 中 |
| `pages/PersonaDetailPage.tsx` | 大 | 中 |
| `pages/CostDashboardPage.tsx` | 中 | 低 |
| `pages/CabinetPage.tsx` | 中 | 低 |
| `pages/ConsultationPage.tsx` | 中 | 低 |
| `pages/HongluisiPage.tsx` | 中 | 低 |
| `pages/TongzhengPage.tsx` | 中 | 低 |

**做法：** 每个 Dashboard 1 个 commit。命名规范：
- 页面标题：复用 `nav.<deptKey>` 或新建 `page.<deptName>.title`
- 区块/卡片：`page.<deptName>.section.*`
- 统计卡片：`stat.<deptName>.*`
- 表格列：`table.<deptName>.column.*`
- 操作：复用 `action.*` / `common.*` / 新建 `page.<deptName>.action.*`

---

### 🟢 批次 5：杂项 + JSON 清理（~300 keys）

**散落组件：**
- `web/src/components/edict/EdictTable.tsx`
- `web/src/components/edict/StatusTag.tsx`
- `web/src/components/edict/FollowUpOverridePanel.tsx`
- `web/src/components/edict/SupervisionReportCard.tsx`
- `web/src/components/edict/OuterLoopTimeline.tsx`
- `web/src/components/edict/NetworkCapabilitySection.tsx`
- `web/src/components/decree/EdictActivityCard.tsx`
- `web/src/components/policy/*.tsx`
- `web/src/components/memorial/UsageDisplay.tsx`
- `web/src/hooks/useWsPolicyToasts.ts`（已部分改）
- 其他 `web/src/components/**/*.tsx`

**JSON 清理（必做）：**
当前三个 locale JSON 中存在 **重复块**（`form` 和 `toast` 各出现两次，后者覆盖前者）。功能不受影响（JSON.parse 后者覆盖前者），但视觉混乱。

清理方法：
1. `Read` 三个 JSON 全文
2. `Write` 干净版本（合并重复块、按字母顺序排列顶层 key）
3. 验证 `npx tsc --noEmit`

---

## 关键技术参考

### 已就位的基建

- **`web/src/hooks/useLocale.ts`**：仿 `useTheme.ts` 模式，`useSyncExternalStore` + Context + localStorage("tianshu-locale")。
- **`web/src/i18n/index.ts`**：
  - `useT()` hook 返回 `(key, vars?) => string`
  - 三级 fallback：当前 locale → zh-classic → raw key
  - 支持 `{var}` 插值（如 `t("event.detail.usagePct", { pct: 50 })`）
- **`web/src/components/layout/LocaleSwitcher.tsx`**：antd `Segmented` 三选一
- **`web/src/App.tsx`**：嵌套 `LocaleContext.Provider`，antd locale 动态选 `enUS` / `zhCN`
- **`web/src/components/layout/AppHeader.tsx`**：右上 `<LocaleSwitcher />`

### 命名规范

```
namespace.section.key        # 标准三级
nav.*                        # 侧边栏 / 主入口
sidebar.*                    # 侧边栏底部按钮
entity.{edict,memorial,decree}    # 业务实体
page.<pageId>.*             # 页面级（标题/区块/Modal）
form.<entityKey>.field.*    # 表单字段 label
form.<entityKey>.placeholder.*
form.<entityKey>.validation.*
form.<entityKey>.tooltip.*
form.<entityKey>.option.*
form.<entityKey>.section.*
table.<entity>.column.*     # 表格列（批次 4 用）
stat.<deptName>.*           # 统计卡片（批次 4 用）
action.*                    # 通用动作（approve/reject/cancel/retry/...）
status.* / phase.* / edictStatus.*    # 业务状态
priority.* / reviewPolicy.* / scheduleType.*    # 枚举值
executionProfile.* / strictness.* / exhaustion.* / criticUnavail.* / checkKind.*
event.label.<eventType>     # 事件标签
event.field.<fieldName>     # 事件 payload 字段
event.group.* / event.detail.* / event.timeline.*
lifecycle.{active,paused,winding_down,complete}
audit.label.{pass,flag,block}
memorial.field.* / memorial.review.pending
pendingTool.*               # 待审批工具卡片
toast.*                     # 消息提示
common.* / common2.*        # 通用词
button.*                    # 按钮专属
unit.*                      # 单位（小时/分钟/秒）
dept.*                      # 部门名（六部）
locale.*                    # LocaleSwitcher 显示名
phaseFilter.* / statusFilter.*    # 筛选下拉的"全部"
empty.*                     # 空状态
```

### 翻译原则

1. **古风词去 zh-modern**：御书房→审批中心 / 敕令→任务 / 奏折→执行记录 / 朱批→审批 / 内阁→规划 / 都察院→审计 ...
2. **状态/抽象词共享**：「运行中」「规划中」「审计中」zh-classic 与 zh-modern 同值，避免硬凑差异
3. **新增 key 优先复用**：`common.*` / `action.*` 常用词不要每页独立 key
4. **静态常量保留作 zh-classic fallback**：避免破坏未迁移的调用方

### 已知陷阱

1. **变量名 `t` shadow**：组件内 `const t = useT()` 后，map/render 函数若有形参 `t` 会 shadow。重写时把内部参数改成 `tool` / `item` / `entry` 等。
2. **JSON unicode 字符 vs 转义序列**：现存代码里有 `"—"` 字面 escape 序列，Edit 时复制原文（不要替换为 `—` 字符）。
3. **`noUncheckedIndexedAccess: true`**：tsconfig 严格模式开启，访问 dict 时返回 `T | undefined`，需处理。
4. **antd `Modal.confirm` 等命令式 API**：当前未迁移，使用 `App.useApp()` 的 message/notification 已经迁移。

### 彩蛋

`locale.zh-classic` 中文显示为 **"彩蛋"**（不是"古风"），英文为 **"Easter Egg"**。**不要改回**，详见 memory `project_locale_classic_easter_egg.md`。

---

## 验证流程

每批完成后：

```bash
cd /Users/chenjiamin/tiangong/tianshu/web
npx tsc --noEmit
# 应只剩 1 个 pre-existing PersonaDetailPage.tsx:455 错误，与 i18n 无关
# 该错误属于本次范围（PersonaDetailPage 在批次 4 内），届时一并修复
```

启动 dev 手测：

```bash
cd /Users/chenjiamin/tiangong/tianshu/web && npm run dev
# 浏览器右上角 Segmented 切换三档：
#   彩蛋 → 古风（御书房/敕令/朱批）
#   通用 → 业界通用（审批中心/任务/审批决策）
#   English → 全英文
# 刷新页面验证 localStorage 持久化
# 切到 English 验证 antd 组件（DatePicker/Pagination）变英文
```

---

## 下次 session 启动指令

打开新 session 后，user 直接说：

> 按 `docs/superpowers/plans/2026-05-06-i18n-migration-handoff.md` 继续 i18n 迁移，从批次 3b（EdictForm）开始。

我（新 session）会：
1. 读这份 handoff 文档
2. 读 EdictForm.tsx 全文
3. 用 `t()` 替换所有中文字面量（JSON 已就位，无需改 JSON）
4. 类型检查 + commit
5. 询问是否继续批次 4

---

## 风险与回滚

| 风险 | 缓解 |
|------|------|
| 单批 commit 改动过大 | 每批单 commit，可单独 revert |
| 翻译质量不一致 | 命名规范集中在本文件，新译者按规范复用 |
| JSON 重复块导致后期 merge 冲突 | 批次 5 cleanup 时一次性整理 |
| EdictForm tooltip 长文本翻译质量 | tooltips 已在 JSON 中预译好，直接复用即可 |

## 完成度

完成 5 批后，估算覆盖率 **>95%**（剩余仅极个别 inline literal 在不常见路径）。
