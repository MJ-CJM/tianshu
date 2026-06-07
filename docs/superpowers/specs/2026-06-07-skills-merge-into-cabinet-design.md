# 秘技台并入藏兵阁、按来源拆分技能管理

> 设计文档 · 2026-06-07

## 背景与问题

Phase 8 新增了独立的"秘技台"页（`web/src/pages/SkillsPage.tsx`，路由 `/skills`，侧边栏入口 `nav.skills`），提供技能的人在回路管控（pin / 归档 / 编辑 / curator 自动迭代）。

但藏兵阁（`web/src/pages/SystemManagementPage.tsx` 的"技能库" tab，`SkillsTab`）**也**在管理技能。两者数据源相同（都用 `useSkills()` 拉**全量**技能），职责重叠，且都把两类来源不同的技能混在一张表里：

- **tianshu 运行时生成/学习的技能** —— curator 自动迭代的产物
- **原本加载进来的技能** —— 预置 / 用户手写 / 注入的技能库

用户诉求：把这两类技能**彻底分开、互不重叠**，不要搞混。

## 目标

1. **秘技** = 只放 tianshu（agent）生成的技能。
2. **技能库** = 只放原本加载进来的技能。
3. 消除"秘技台独立页"与"藏兵阁技能库 tab"的重叠 —— 秘技台并入藏兵阁，成为与"技能库"并列的 tab。

## 非目标

- 不改 `GET /skills` 后端接口（已返回区分所需字段）。
- 不改 curator / loader / guard 等技能后端逻辑。
- 不重写藏兵阁现有的其它 tab（工具箱 / MCP / Prompt / 供应 / 插件 / 配置 / 外部凭证）。
- 本次不写前端测试（遵循"功能优先、测试最后补"）。

## 区分逻辑（"不搞混"的唯一依据）

后端 `GET /skills` 返回的每条技能已带 `created_by` 与 `source` 字段（见 `web/src/api/types.ts` 的 `SkillInfo`）：

| 类别 | 判定 | 取值 |
|---|---|---|
| **tianshu 生成的技能** | `created_by === "agent"` | curator 迭代的对象 |
| **原本加载的技能** | `created_by !== "agent"` | `source` ∈ `builtin` / `user` / `workspace` / `injected` |

过滤在**前端**完成 —— 两个 tab 都调用 `useSkills()` 拿全量，各自按 `created_by` 筛选。**后端无需改动。**

## 改造方案

### 1. 藏兵阁内两个并列 tab（互斥）

| tab | 数据过滤 | 列 & 功能 | 来源 |
|---|---|---|---|
| **技能库**（现有 `SkillsTab`）| `created_by !== "agent"` | name / description / source / tool_tier / 字符数 + 新建·编辑·删除 | 现状沿用，仅加过滤 |
| **习得技能 / 秘技**（新 `SecretSkillsTab`）| `created_by === "agent"` | name / description / source / state / usage / flags / created_at + pin·编辑·归档 | 从 `SkillsPage.tsx` 迁移 |

- tab 顺序：`技能库 → 习得技能 → 工具箱 → MCP → Prompt → 供应 → 插件 → 配置 → 外部凭证`（习得技能紧跟技能库）。
- 默认 tab 仍为"技能库"（`defaultActiveKey="skills"` 不变）。

### 2. 组件抽取

`SystemManagementPage.tsx` 已 2031 行（远超项目 800 行上限）。新 tab 内容**不再内联**，与 `MCPTab` 同构，抽成独立文件：

- 新建 `web/src/components/system/SecretSkillsTab.tsx` —— 内容来自 `SkillsPage.tsx`（表格列 + pin/归档/编辑 handlers + `SkillEditDialog`），并在数据上加 `created_by === "agent"` 过滤。
- 在 `SystemManagementPage.tsx` 的 Tabs `items` 中，于 `skills` 与 `tools` 之间插入 `secret-skills` 项，`children: <SecretSkillsTab />`。

### 3. 技能库 tab（`SkillsTab`）改动

- 数据加过滤：`(skills ?? []).filter((s) => s.created_by !== "agent")`。
- 其余列与"新建/编辑/删除"功能不变（"新建技能"产出的是用户手写技能，仍归技能库）。

### 4. 删除独立秘技台入口

- `web/src/App.tsx`：删除 `<Route path="/skills" element={<SkillsPage />} />` 及 `import SkillsPage`。
- `web/src/components/layout/AppSidebar.tsx`：删除 `key: "/skills"` 菜单项（`BulbOutlined` / `nav.skills`）。
- 删除 `web/src/pages/SkillsPage.tsx`（内容已迁入 `SecretSkillsTab`）。

### 5. i18n（三套 locale：`zh-modern` / `zh-classic` / `en`）

- 新增 tab label `system.tab.secretSkills`：
  - zh-modern = `习得技能`
  - zh-classic = `秘技`
  - en = `Learned Skills`
- 将 `skillsPage.*` 文案块整体迁移为 `system.secretSkills.*`（被 `SecretSkillsTab` 复用），组件内 `t("skillsPage.xxx")` 调用相应改为 `t("system.secretSkills.xxx")`。
- 删除 `nav.skills`（三套）。

## 文件改动清单

| 文件 | 改动 |
|---|---|
| `web/src/components/system/SecretSkillsTab.tsx` | **新建**，迁移自 `SkillsPage.tsx` + `created_by==="agent"` 过滤 |
| `web/src/pages/SystemManagementPage.tsx` | `SkillsTab` 加 `created_by!=="agent"` 过滤；Tabs items 插入 `secret-skills` |
| `web/src/pages/SkillsPage.tsx` | **删除** |
| `web/src/App.tsx` | 删 `/skills` Route 与 import |
| `web/src/components/layout/AppSidebar.tsx` | 删 `/skills` 菜单项 |
| `web/src/i18n/locales/{zh-modern,zh-classic,en}.json` | 新增 `system.tab.secretSkills`；`skillsPage.*` → `system.secretSkills.*`；删 `nav.skills` |

## 边界与风险

- **秘技 tab 为空**：agent 尚未生成任何技能时，表格走空状态（antd Table 默认空提示），无需特殊处理。
- **技能库 tab 过滤后**：source 列不会再出现 agent，但 `builtin/user/workspace/injected` 仍正常显示，`source` 列保留原样即可。
- **pin/归档语义**：仅对 agent 生成的技能有意义，故只在习得技能 tab 提供；技能库 tab 不受影响。
- **`SkillEditDialog`** 已是独立组件（`web/src/components/skill/SkillEditDialog.tsx`），随 `SecretSkillsTab` 复用，无需改动。
- **路由清理**：删除 `/skills` 后，任何残留的指向 `/skills` 的链接（若有）需一并清理 —— 实现阶段全局搜一次 `"/skills"` 确认无遗漏。

## 验证

- 前端构建通过（`tsc` 无类型错误，无悬挂 import）。
- 手动验证：藏兵阁打开 → "技能库" tab 只见加载的技能、"习得技能" tab 只见 agent 生成的技能；侧边栏不再有独立秘技台入口；三套 locale 切换标签正确。
