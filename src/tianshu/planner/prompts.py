"""Planner prompt templates — dynamic prompt construction with persona injection."""

from __future__ import annotations

from tianshu.persona.model import DEFAULT_EXECUTOR_ID, AgentPersona

PLANNING_USER_TEMPLATE = """\
敕令旨意：{goal}

附则：{context}

约束条件：{constraints}

期望输出格式：{output_format}
"""

_PLANNING_ROLE = """\
# 规划职责

你现在担任规划职责。根据敕令内容，判断复杂度并决定处理方式。

## 规划策略

### 简单任务（直接执行）
以下情况应生成单一任务，不做分解：
- 简单问答（如"如何查看天气？"、"今天几号？"）
- 单步操作（如"读取某文件"、"查询某数据"）
- 信息查询、翻译、总结、格式转换
→ 输出 1 个任务，task_id 为 "main"

### 复杂任务（需要分解）
以下情况应分解为多个子任务：
- 多步骤协同（如"重构某模块并补充测试"）
- 涉及调研 → 方案 → 执行 → 验证的流程
- 需要不同专长官员分别处理
→ 输出 2-8 个任务，用 depends_on 表达依赖

## 输出格式

返回 JSON（不要加 markdown 代码块）：

{
  "tasks": [
    {
      "task_id": "main",
      "description": "具体任务描述",
      "depends_on": [],
      "tools_required": [],
      "assigned_official": "__EXEC_ID__",
      "can_run_parallel": false,
      "estimated_tokens": 1000
    }
  ],
  "priority_order": ["main"]
}

### 简单任务示例（工具/操作类 → 执行型官员）

输入：如何查看天气？
输出：
{
  "tasks": [
    {
      "task_id": "main",
      "description": "回答用户关于如何查看天气的问题",
      "depends_on": [],
      "tools_required": [],
      "assigned_official": "__EXEC_ID__",
      "can_run_parallel": false,
      "estimated_tokens": 500
    }
  ],
  "priority_order": ["main"]
}

### 简单任务示例（历史/知识类 → 学者型官员）

输入：宋朝持续了多少年？
输出：
{
  "tasks": [
    {
      "task_id": "main",
      "description": "回答宋朝国祚年数这一历史知识问题",
      "depends_on": [],
      "tools_required": [],
      "assigned_official": "__DOC_ID__",
      "can_run_parallel": false,
      "estimated_tokens": 500
    }
  ],
  "priority_order": ["main"]
}

### 复杂任务示例

输入：重构 auth 模块，补充单元测试，更新 API 文档
输出：
{
  "tasks": [
    {
      "task_id": "t1",
      "description": "分析现有 auth 模块结构，确定重构方案",
      "depends_on": [],
      "tools_required": ["read_file", "shell_exec"],
      "assigned_official": "__EXEC_ID__",
      "can_run_parallel": false,
      "estimated_tokens": 2000
    },
    {
      "task_id": "t2",
      "description": "执行 auth 模块重构",
      "depends_on": ["t1"],
      "tools_required": ["read_file", "write_file", "shell_exec"],
      "assigned_official": "__EXEC_ID__",
      "can_run_parallel": false,
      "estimated_tokens": 4000
    },
    {
      "task_id": "t3",
      "description": "补充 auth 模块单元测试",
      "depends_on": ["t2"],
      "tools_required": ["read_file", "write_file", "shell_exec"],
      "assigned_official": "__EXEC_ID__",
      "can_run_parallel": false,
      "estimated_tokens": 3000
    },
    {
      "task_id": "t4",
      "description": "更新 API 文档",
      "depends_on": ["t2"],
      "tools_required": ["read_file", "write_file"],
      "assigned_official": "__DOC_ID__",
      "can_run_parallel": true,
      "estimated_tokens": 1500
    }
  ],
  "priority_order": ["t1", "t2", "t3", "t4"]
}

（以上示例中的 assigned_official 仅为格式演示，实际值必须从「可用执行官」名册的 ID 中选取。）
"""


def _example_official_ids(personas: list[AgentPersona] | None) -> tuple[str, str]:
    """示例任务的 assigned_official：从真实名册取执行型/文书型官员 id。

    示例硬编码部门 id（bingbu/wenyuan）会诱导 LLM 照抄无效值——名册里是
    官员 id 而非部门 id，照抄即触发 selector 兜底重派。无名册时维持旧占位。
    """
    exec_id, doc_id = DEFAULT_EXECUTOR_ID, "wenyuan"
    if personas:
        exec_id = next((p.id for p in personas if p.department == "bingbu"), personas[0].id)
        doc_id = next((p.id for p in personas if p.department == "wenyuan"), exec_id)
    return exec_id, doc_id


def build_planning_prompt(
    persona_context: str,
    officials_roster: str,
    tools_list: str,
    example_personas: list[AgentPersona] | None = None,
) -> str:
    """Assemble the full planning system prompt from dynamic components."""
    exec_id, doc_id = _example_official_ids(example_personas)
    role = _PLANNING_ROLE.replace("__EXEC_ID__", exec_id).replace("__DOC_ID__", doc_id)

    parts: list[str] = []

    if persona_context:
        parts.append(persona_context)

    parts.append(role)

    if officials_roster:
        parts.append(officials_roster)

    if tools_list:
        parts.append(tools_list)

    return "\n\n".join(parts)


# 部门专长注记：择官判据（原内阁派官 prompt 的拣选规则，随派官并入规划后迁于此）。
# 部门是种子常量（storage/migrations.py）；未知部门留空不误导。
_DEPT_HINTS: dict[str, str] = {
    "bingbu": "执行型——工具操作/工程/部署/构建类任务",
    "wenyuan": "学者型——历史/知识问答/文档/翻译/总结类问题",
    "ducha": "监察型——审计/核查/验证",
    "hubu": "度支型——成本/预算/账务",
    "tongzheng": "传达型——通知/报告/对外沟通",
}


def format_officials_roster(personas: list[AgentPersona]) -> str:
    """Format executor personas as a Markdown table for the LLM."""
    if not personas:
        return ""

    lines = [
        "# 可用执行官",
        "",
        "| ID | 名称 | 部门（专长） | 可用工具 | 可委派至 |",
        "|----|------|--------------|----------|----------|",
    ]
    for p in personas:
        tools_str = ", ".join(p.tools_allowed) if p.tools_allowed else "（默认工具）"
        delegates_str = ", ".join(p.delegates_to) if p.can_delegate and p.delegates_to else "—"
        name = f"{p.name}·{p.title}" if p.title else p.name
        hint = _DEPT_HINTS.get(p.department)
        dept = f"{p.department}（{hint}）" if hint else p.department
        lines.append(f"| {p.id} | {name} | {dept} | {tools_str} | {delegates_str} |")

    lines.append("")
    lines.append("assigned_official 必须从上表 ID 中选取。")
    lines.append(
        "拣选规则：按部门专长择官——历史/知识问答/文档类问题选学者型官员，"
        "工具执行/工程任务选执行型官员，审计核查选监察官员。"
        "简单问答同样按专长拣选，不要一律派给执行型官员；官员姓名只是代号，不代表其专长。"
    )
    lines.append("可委派至非空的官员能在执行时将子任务转交给对应官员，适合分配复杂任务。")
    return "\n".join(lines)


def format_tools_list(tool_names: list[str]) -> str:
    """Format available tool names as a prompt section."""
    if not tool_names:
        return "# 可用工具\n\n当前无可用工具，所有任务的 tools_required 应为 []。"

    lines = ["# 可用工具", "", "tools_required 只能从以下列表中选取，不需要工具则填 []：", ""]
    for name in sorted(tool_names):
        lines.append(f"- {name}")
    return "\n".join(lines)
