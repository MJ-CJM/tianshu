# 自然语言下发敕令设计：解析端点 + 预填表单

> 日期：2026-05-21
> 状态：设计已批准，待写实现计划

## 背景与动机

Web 端创建敕令（`EdictCreatePage` / `EdictForm`）目前是一个字段很多的大表单：标题、
旨意、执行方式、规划审批、附则、调度、高级选项（runtime）、长任务模式（acceptance）。
对常见任务而言配置项过多、上手成本高。

后端其实已具备自然语言下发能力：`submit_edict` 工具让助手 LLM 在对话中"颁敕"，飞书侧
（assistant 分支 + edict_bridge）已能用自然语言创建敕令（含定时）。**缺口在 web：web
没有任何对话/自然语言入口，只有大表单。**

参考 hermes / openclaw 的"自然语言触发定时任务"体验，目标：

- 在 web 端支持"用一句话描述 → 自动填表 → 用户确认 → 颁发"的快捷路径。
- 保留现有完整表单作为审阅/编辑面（不替换）。
- 顺带精简表单：默认只露核心，其余折叠。
- 整体可用性更好，不增加大体量子系统（不做多轮聊天 UI）。

## 范围

**纳入**：

- 新增解析端点 `POST /api/edicts/parse`：自然语言 → 结构化敕令草稿（不落库）。
- 前端 `EdictCreatePage` 顶部加自然语言输入框 + "智能填充"，把草稿预填进现有表单。
- 表单精简：默认只露"旨意 + 调度"，其余折叠进"更多设置"。
- 时区处理复用既有修复（naive 时间按 Asia/Shanghai 解释）。

**不纳入**：

- 不做多轮对话助手 / 聊天 UI（用户明确"不要太复杂"）。
- 不自动提交（始终预填 + 用户确认）。
- 不改 `submit_edict` 工具与飞书路径（它们已可用）。
- 不重做表单为"双模式切换"。

## 交互模型（已确认）

**一句话 → 自动填表 → 用户确认。** 解析纯只读、不创建敕令；用户在表单里检查/微调后
点"颁发敕令"，走现有 `POST /api/edicts`。解析失败一律安全回落到手动表单。

## 架构总览

只新增一个后端解析端点 + 一段前端预填逻辑，其余全部复用：

- 解析：`POST /api/edicts/parse` → 调一次廉价 LLM → 返回草稿 JSON + 识别说明。
- 创建：仍走现有 `POST /api/edicts`（`EdictCreateRequest`）。
- 表单：现有 `EdictForm` 作为审阅/编辑面，仅调整默认折叠结构。

## 组件设计

### 1. 解析端点

`src/tianshu/gateway/api.py` 新增（或就近新文件）：

```
POST /api/edicts/parse
  body: { "text": str }                       # 自然语言描述
  resp: { "success": true,
          "data": { "draft": <EdictDraft>, "notes": str } }
```

- `EdictDraft` 是 `EdictCreateRequest` 常用字段的子集（pydantic 模型，全部 optional 除
  `goal`）：`title?`, `goal`, `context?`, `schedule{type, cron?, at?, timezone?}?`,
  `priority?`, `execution_profile?`。**不解析 runtime / acceptance 细节**——长任务、超时、
  预算等高级配置一律留表单手填。若用户自然语言里明显提到"反复迭代到满意/长任务"，仅在
  `notes` 里提示"这看起来像长任务，可在'更多设置'里开启长任务模式"，draft 本身不设
  acceptance。
- `notes`：一句中文说明 LLM 识别到了什么（"已识别：每天 18:00 推送 → cron `0 18 * * *`，
  时区 Asia/Shanghai"），前端作为提示条显示。
- LLM 调用：`request.app.state.provider_manager.get_client(config_name_override="deepseek-flash")`，
  取不到该配置则回退默认 `get_client()`。system prompt 要求**仅输出 JSON**，附 schema 说明
  与"只填用户明确表达的字段，其余留空"的指令。
- 防御式校验：
  - `schedule.cron` 用 `croniter` 校验，非法则丢弃 cron 并在 notes 标注"时间未识别"。
  - `schedule.at` 用 ISO 校验；naive 时间不在端点补时区（交给创建端点既有的
    naive→Asia/Shanghai 逻辑），但 notes 提示按北京时间理解。
  - `priority` / `execution_profile` 按枚举校验，越界丢弃。
- 失败路径：LLM 异常或返回非法 JSON → 返回 HTTP 422 + 友好 message；不抛 500。

### 2. 前端入口（EdictCreatePage 顶部）

`web/src/pages/EdictCreatePage.tsx` + `web/src/components/edict/EdictForm.tsx` +
`web/src/api/edicts.ts`：

- 新增 API 封装 `parseEdict(text) -> { draft, notes }`。
- 表单顶部加一块"自然语言下发"：一个 `Input.TextArea` + "智能填充"按钮 + loading 态。
- 点击 → 调 `parseEdict` →
  - 成功：用 antd `form.setFieldsValue` 填入草稿字段；草稿里若含调度/高级/长任务字段，
    自动展开对应折叠区；把 `notes` 显示为 `Alert`/提示条。
  - 空草稿/失败：显示"没太理解，请手动填写或换种说法"，表单保持可手动操作。
- 始终不自动提交：用户检查无误后点现有"颁发敕令"。
- 提供"清空"以便重填。

### 3. 表单精简（默认折叠）

调整 `EdictForm` 的默认可见结构：

- **默认可见**：敕令旨意（goal）、调度方式（+ 时间/cron 输入）。
- **折叠进"更多设置"**：标题、执行方式（内阁决策/直接指派）、规划审批、附则、
  高级选项（runtime）、长任务模式（Outer Loop）。
- 实现：用已有的 `Collapse` 把这些项收进一个"更多设置"面板（高级选项/长任务可作为其内
  子项或并列子面板）。NL 预填命中其中字段时自动展开，保证用户看得见替它填了什么。
- 不改任何字段的提交语义，仅改默认展开/折叠。

## 数据流

```
用户输入自然语言
  → POST /api/edicts/parse
  → provider_manager.get_client(deepseek-flash).chat(system+user)
  → 解析+校验 LLM JSON → { draft, notes }
  → 前端 form.setFieldsValue(draft) + 展开命中折叠区 + 显示 notes
  → 用户审阅/微调
  → 点"颁发敕令" → POST /api/edicts（现有路径）
```

## 错误处理

- LLM 不可用 / 超时 → 端点 422，前端提示并保留手动表单。
- 返回非 JSON / schema 不符 → 端点尽量提取可用字段；完全失败则 422。
- 时间未识别 → schedule 留空 + notes 提示"未识别出时间，请手动选择"。
- 解析出的非法枚举/cron → 丢弃该字段，不阻断整体草稿。

## 测试策略

遵循项目"功能优先、测试最后补"约定：

- **后端单测**（mock provider_manager 的 client.chat 返回固定 JSON）：
  - 正常：cron 描述 → draft.schedule.type=cron + cron 校验通过 + timezone=Asia/Shanghai。
  - 一次性：含日期时间 → draft.schedule.type=once + at 为 ISO。
  - 坏 JSON → 422。
  - 非法 cron / 非法 priority → 被丢弃，draft 仍返回其余字段。
- **前端**：轻量验证 setFieldsValue 填充 + 折叠展开 + 失败提示（可后补）。

## 风险与权衡

- **解析不准**：LLM 可能误解时间/调度。缓解：预填 + 用户确认（永不自动提交），notes 透明
  说明识别结果，未识别字段留空让用户补。
- **模型成本**：一次短补全，廉价模型，成本极小，纳入现有 cost 体系。
- **表单折叠**改变默认视图：仅影响默认展开状态，不动字段与提交语义，回归风险低。
