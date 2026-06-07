# 秘技台并入藏兵阁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把独立的"秘技台"页并入"藏兵阁"，藏兵阁内用"技能库 / 习得技能"两个 tab 按 `created_by` 互斥分管两类技能。

**Architecture:** 纯前端改造。两 tab 都调用现有 `useSkills()` 拿全量技能，各自前端过滤：技能库 `created_by !== "agent"`，习得技能 `created_by === "agent"`。秘技台内容抽成 `SecretSkillsTab` 组件并入藏兵阁，删除独立 `/skills` 路由与侧边栏入口。后端不动。

**Tech Stack:** React + TypeScript + antd + react-query；i18n 三套 locale（zh-modern / zh-classic / en）。

**对 spec 的实现细化：** `skillsPage.*` 文案块保留为顶层键，由 `SecretSkillsTab` 直接复用（`t("skillsPage.*")`），不迁移进 `system.secretSkills` —— 零行为差异、最小改动。仅新增 tab 标签 `system.tab.secretSkills`、删除 `nav.skills`。

**测试策略：** 遵循项目偏好"功能优先、测试最后补"，本次不写前端测试；以 TypeScript 编译 + 构建 + 手动验证为验收。

---

### Task 1: i18n —— 新增 tab 标签、删除 nav.skills（三套 locale）

**Files:**
- Modify: `web/src/i18n/locales/zh-modern.json`
- Modify: `web/src/i18n/locales/zh-classic.json`
- Modify: `web/src/i18n/locales/en.json`

- [ ] **Step 1: zh-modern —— system.tab 加 secretSkills**

把（`web/src/i18n/locales/zh-modern.json` 第 1628-1637）：

```json
    "tab": {
      "skills": "技能库",
      "tools": "工具箱",
```

改为：

```json
    "tab": {
      "skills": "技能库",
      "secretSkills": "习得技能",
      "tools": "工具箱",
```

- [ ] **Step 2: zh-modern —— 删除 nav.skills**

把（第 17-18 行）：

```json
    "sessionRules": "会话规则",
    "skills": "技能管控"
  },
```

改为：

```json
    "sessionRules": "会话规则"
  },
```

- [ ] **Step 3: zh-classic —— system.tab 加 secretSkills**

把（`web/src/i18n/locales/zh-classic.json`）：

```json
    "tab": {
      "skills": "技能库",
      "tools": "工具箱",
```

改为：

```json
    "tab": {
      "skills": "技能库",
      "secretSkills": "秘技",
      "tools": "工具箱",
```

- [ ] **Step 4: zh-classic —— 删除 nav.skills**

把：

```json
    "sessionRules": "权印司",
    "skills": "秘技台"
  },
```

改为：

```json
    "sessionRules": "权印司"
  },
```

- [ ] **Step 5: en —— system.tab 加 secretSkills**

把（`web/src/i18n/locales/en.json`）：

```json
    "tab": {
      "skills": "Skills",
      "tools": "Tools",
```

改为：

```json
    "tab": {
      "skills": "Skills",
      "secretSkills": "Learned Skills",
      "tools": "Tools",
```

- [ ] **Step 6: en —— 删除 nav.skills**

把：

```json
    "sessionRules": "Session Rules",
    "skills": "Skills Control"
  },
```

改为：

```json
    "sessionRules": "Session Rules"
  },
```

- [ ] **Step 7: 校验三套 JSON 合法**

Run: `cd web && node -e "['zh-modern','zh-classic','en'].forEach(f=>{require('./src/i18n/locales/'+f+'.json');console.log(f,'OK')})"`
Expected: 三行 `... OK`，无 SyntaxError。

- [ ] **Step 8: Commit**

```bash
git add web/src/i18n/locales/zh-modern.json web/src/i18n/locales/zh-classic.json web/src/i18n/locales/en.json
git commit -m "feat(skills): 藏兵阁新增 secretSkills tab 标签，删除独立秘技台 nav 入口文案"
```

---

### Task 2: 新建 SecretSkillsTab 组件（从 SkillsPage 迁移 + agent 过滤）

**Files:**
- Create: `web/src/components/system/SecretSkillsTab.tsx`

- [ ] **Step 1: 创建组件文件**

内容（来自 `web/src/pages/SkillsPage.tsx`，改动：去掉 `PageContainer` 改用 `<>`；import 路径相对 `components/system/` 调整；数据加 `created_by === "agent"` 过滤；保留 `t("skillsPage.*")` 键名）：

```tsx
import { useState, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Table,
  Tag,
  Button,
  Space,
  Popconfirm,
  Typography,
  notification,
  Tooltip,
} from "antd";
import {
  PushpinOutlined,
  PushpinFilled,
  EditOutlined,
  RollbackOutlined,
} from "@ant-design/icons";
import SkillEditDialog from "../skill/SkillEditDialog";
import { useSkills } from "../../hooks/useSystem";
import { archiveSkill, pinSkill } from "../../api/system";
import type { SkillInfo } from "../../api/types";
import { useT } from "../../i18n";

export default function SecretSkillsTab() {
  const t = useT();
  const qc = useQueryClient();
  const { data: skills, isLoading } = useSkills();
  const [editName, setEditName] = useState<string | null>(null);

  // 只放 tianshu(agent) 运行时生成/学习的技能
  const agentSkills = (skills ?? []).filter((s) => s.created_by === "agent");

  // Sort: by created_at desc (missing created_at goes last)
  const sorted = [...agentSkills].sort((a, b) => {
    if (a.created_at && b.created_at) {
      return b.created_at.localeCompare(a.created_at);
    }
    if (a.created_at) return -1;
    if (b.created_at) return 1;
    return 0;
  });

  const refresh = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["skills"] });
  }, [qc]);

  const handleArchive = async (name: string) => {
    await archiveSkill(name);
    notification.success({ message: t("skillsPage.toast.archived", { name }) });
    refresh();
  };

  const handlePin = async (name: string, currentPinned: boolean) => {
    await pinSkill(name, !currentPinned);
    notification.success({
      message: !currentPinned
        ? t("skillsPage.toast.pinned", { name })
        : t("skillsPage.toast.unpinned", { name }),
    });
    refresh();
  };

  const columns = [
    {
      title: t("skillsPage.table.name"),
      dataIndex: "name",
      key: "name",
      render: (name: string) => <Typography.Text strong>{name}</Typography.Text>,
    },
    {
      title: t("skillsPage.table.description"),
      dataIndex: "description",
      key: "description",
      ellipsis: true,
    },
    {
      title: t("skillsPage.table.source"),
      key: "source",
      width: 110,
      render: (_: unknown, record: SkillInfo) => {
        if (record.created_by === "agent") {
          return (
            <Tag color="volcano">{t("skillsPage.badge.autoCurated")}</Tag>
          );
        }
        const colorMap: Record<string, string> = {
          builtin: "blue",
          user: "cyan",
          workspace: "green",
          injected: "purple",
        };
        return (
          <Tag color={colorMap[record.source] ?? "default"}>{record.source}</Tag>
        );
      },
    },
    {
      title: t("skillsPage.table.state"),
      dataIndex: "state",
      key: "state",
      width: 90,
      render: (state: string | undefined) => {
        if (!state) return "—";
        const color = state === "active" ? "green" : state === "archived" ? "default" : "orange";
        return <Tag color={color}>{state}</Tag>;
      },
    },
    {
      title: t("skillsPage.table.usage"),
      key: "usage",
      width: 120,
      render: (_: unknown, record: SkillInfo) => {
        if (record.usage_count == null) return "—";
        const rate =
          record.success_rate != null
            ? `${(record.success_rate * 100).toFixed(0)}%`
            : "—";
        return (
          <Typography.Text style={{ fontSize: 12 }}>
            {record.usage_count} / {rate}
          </Typography.Text>
        );
      },
    },
    {
      title: t("skillsPage.table.flags"),
      key: "flags",
      width: 120,
      render: (_: unknown, record: SkillInfo) => (
        <Space size={4}>
          {record.human_curated && (
            <Tooltip title={t("skillsPage.badge.humanCurated")}>
              <Tag color="gold">{t("skillsPage.badge.humanCuratedShort")}</Tag>
            </Tooltip>
          )}
          {record.pinned && (
            <Tooltip title={t("skillsPage.badge.pinned")}>
              <Tag color="geekblue">{t("skillsPage.badge.pinnedShort")}</Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: t("skillsPage.table.createdAt"),
      dataIndex: "created_at",
      key: "created_at",
      width: 150,
      render: (v: string | undefined) =>
        v ? new Date(v).toLocaleString("zh-CN") : "—",
    },
    {
      title: t("skillsPage.table.actions"),
      key: "actions",
      width: 140,
      render: (_: unknown, record: SkillInfo) => (
        <Space size={4}>
          <Tooltip
            title={
              record.pinned ? t("skillsPage.action.unpin") : t("skillsPage.action.pin")
            }
          >
            <Button
              size="small"
              type="text"
              icon={record.pinned ? <PushpinFilled /> : <PushpinOutlined />}
              onClick={() => handlePin(record.name, record.pinned ?? false)}
            />
          </Tooltip>
          <Tooltip title={t("skillsPage.action.edit")}>
            <Button
              size="small"
              type="text"
              icon={<EditOutlined />}
              onClick={() => setEditName(record.name)}
            />
          </Tooltip>
          <Popconfirm
            title={t("skillsPage.confirmArchive")}
            onConfirm={() => handleArchive(record.name)}
            okText={t("skillsPage.action.archive")}
            cancelText={t("common.cancel")}
          >
            <Tooltip title={t("skillsPage.action.archive")}>
              <Button
                size="small"
                type="text"
                danger
                icon={<RollbackOutlined />}
              />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Table
        dataSource={sorted}
        columns={columns}
        rowKey="name"
        loading={isLoading}
        size="small"
        pagination={false}
      />
      <SkillEditDialog
        name={editName}
        onClose={() => setEditName(null)}
        onSaved={refresh}
      />
    </>
  );
}
```

- [ ] **Step 2: 类型检查**

Run: `cd web && npx tsc --noEmit`
Expected: 无错误（此时 `SkillsPage.tsx` 仍存在且未被引用处理，下一任务接线，本步仅确认新组件本身类型正确）。

- [ ] **Step 3: Commit**

```bash
git add web/src/components/system/SecretSkillsTab.tsx
git commit -m "feat(skills): 新建 SecretSkillsTab（仅展示 agent 生成的技能，含 pin/归档/编辑）"
```

---

### Task 3: 藏兵阁接线 —— 技能库加过滤、插入习得技能 tab

**Files:**
- Modify: `web/src/pages/SystemManagementPage.tsx`

- [ ] **Step 1: import SecretSkillsTab**

在文件顶部 import 区，紧随 `import MCPTab from "../components/system/MCPTab";`（第 41 行）后新增一行：

```tsx
import MCPTab from "../components/system/MCPTab";
import SecretSkillsTab from "../components/system/SecretSkillsTab";
```

- [ ] **Step 2: SkillsTab 数据加 `created_by !== "agent"` 过滤**

在 `SkillsTab` 内，把（第 97 行）：

```tsx
  const { data: skills, isLoading } = useSkills();
```

改为：

```tsx
  const { data: skills, isLoading } = useSkills();
  // 技能库只展示原本加载进来的技能；agent 生成的归"习得技能" tab
  const loadedSkills = (skills ?? []).filter((s) => s.created_by !== "agent");
```

- [ ] **Step 3: SkillsTab 的 totalChars 基于过滤后数据**

把（第 150-153 行附近）：

```tsx
  const totalChars = (skills ?? []).reduce(
```

改为：

```tsx
  const totalChars = loadedSkills.reduce(
```

> 注意：保留原 reduce 的累加回调与初始值不变，仅替换被 reduce 的数组来源。

- [ ] **Step 4: SkillsTab 的 Table dataSource 用过滤后数据**

把（第 250 行）：

```tsx
      <Table
        dataSource={skills}
```

改为：

```tsx
      <Table
        dataSource={loadedSkills}
```

- [ ] **Step 5: Tabs items 在 skills 与 tools 之间插入 secret-skills**

把（第 1987-1996 行）：

```tsx
          {
            key: "skills",
            label: t("system.tab.skills"),
            children: <SkillsTab />,
          },
          {
            key: "tools",
            label: t("system.tab.tools"),
            children: <ToolsTab />,
          },
```

改为：

```tsx
          {
            key: "skills",
            label: t("system.tab.skills"),
            children: <SkillsTab />,
          },
          {
            key: "secret-skills",
            label: t("system.tab.secretSkills"),
            children: <SecretSkillsTab />,
          },
          {
            key: "tools",
            label: t("system.tab.tools"),
            children: <ToolsTab />,
          },
```

- [ ] **Step 6: 类型检查**

Run: `cd web && npx tsc --noEmit`
Expected: 无错误。

- [ ] **Step 7: Commit**

```bash
git add web/src/pages/SystemManagementPage.tsx
git commit -m "feat(skills): 藏兵阁技能库 tab 仅留加载技能，并入习得技能 tab"
```

---

### Task 4: 删除独立秘技台入口

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/layout/AppSidebar.tsx`
- Delete: `web/src/pages/SkillsPage.tsx`

- [ ] **Step 1: App.tsx 删除 SkillsPage import**

把（第 27 行）：

```tsx
import SkillsPage from "./pages/SkillsPage";
```

删除（整行移除）。

- [ ] **Step 2: App.tsx 删除 /skills Route**

把（第 70 行）：

```tsx
                <Route path="/skills" element={<SkillsPage />} />
```

删除（整行移除）。

- [ ] **Step 3: AppSidebar.tsx 删除 /skills 菜单项**

把（第 111-115 行）：

```tsx
    {
      key: "/skills",
      icon: <BulbOutlined />,
      label: t("nav.skills"),
    },
```

删除（整段移除）。

- [ ] **Step 4: AppSidebar.tsx 删除现已未用的 BulbOutlined import**

把（第 26 行）：

```tsx
  BulbOutlined,
```

删除（整行移除）。
> `BulbOutlined` 仅在第 113 行使用（已于上一步删除），故 import 一并移除以免 lint/tsc 报未用变量。

- [ ] **Step 5: 删除 SkillsPage.tsx 文件**

```bash
git rm web/src/pages/SkillsPage.tsx
```

- [ ] **Step 6: 全局确认无 /skills 残留引用**

Run: `grep -rn "/skills\b\|SkillsPage" web/src`
Expected: 无输出（除本计划/文档外，源码无残留）。若有输出，逐处清理。

- [ ] **Step 7: 类型检查 + 构建**

Run: `cd web && npx tsc --noEmit && npm run build`
Expected: tsc 无错误；构建成功产出 dist。

- [ ] **Step 8: Commit**

```bash
git add web/src/App.tsx web/src/components/layout/AppSidebar.tsx
git commit -m "feat(skills): 删除独立秘技台路由、侧边栏入口与 SkillsPage 页面"
```

---

### Task 5: 手动验证

**Files:** 无（验收）

- [ ] **Step 1: 启动前端并打开藏兵阁**

启动开发服务（如 `cd web && npm run dev`），登录后进入"藏兵阁/系统管理"。

- [ ] **Step 2: 核对两 tab 互斥**

预期：
- "技能库" tab 只见加载的技能（source = builtin/user/workspace/injected），不含 agent 生成的。
- "习得技能/秘技" tab 只见 agent 生成的技能（source 显示"自动固化/机器铸造"），且 pin/编辑/归档可用。
- 侧边栏不再有独立的"秘技台/技能管控/Skills Control"入口。

- [ ] **Step 3: 三套 locale 切换核对标签**

切换 zh-modern / zh-classic / en，确认新 tab 标签分别为"习得技能 / 秘技 / Learned Skills"，且无遗留指向旧秘技台入口。

---

## Self-Review

- **Spec coverage：** 区分逻辑（Task 2/3 过滤）✓；两 tab 互斥（Task 3）✓；组件抽取（Task 2）✓；删除独立入口（Task 4）✓；i18n 三套（Task 1）✓；不改后端 ✓；不写测试 ✓。spec 的"迁移 skillsPage→system.secretSkills"已在计划头部明确改为"保留 skillsPage 键复用"，行为等价。
- **Placeholder scan：** 无 TBD/TODO；每个代码步均给出完整代码或确切 Edit。
- **Type consistency：** 组件名 `SecretSkillsTab` 全程一致；过滤变量 `loadedSkills`（技能库）/ `agentSkills`（习得技能）命名一致；复用 `t("skillsPage.*")` 键名与保留的顶层块一致；tab key `secret-skills` 与标签键 `system.tab.secretSkills` 对应。
