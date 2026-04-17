"""Planner prompt templates — dynamic prompt construction with persona injection."""

from __future__ import annotations

from tianshu.persona.model import AgentPersona

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
      "assigned_official": "bingbu",
      "can_run_parallel": false,
      "estimated_tokens": 1000
    }
  ],
  "priority_order": ["main"]
}

### 简单任务示例

输入：如何查看天气？
输出：
{
  "tasks": [
    {
      "task_id": "main",
      "description": "回答用户关于如何查看天气的问题",
      "depends_on": [],
      "tools_required": [],
      "assigned_official": "bingbu",
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
      "assigned_official": "bingbu",
      "can_run_parallel": false,
      "estimated_tokens": 2000
    },
    {
      "task_id": "t2",
      "description": "执行 auth 模块重构",
      "depends_on": ["t1"],
      "tools_required": ["read_file", "write_file", "shell_exec"],
      "assigned_official": "bingbu",
      "can_run_parallel": false,
      "estimated_tokens": 4000
    },
    {
      "task_id": "t3",
      "description": "补充 auth 模块单元测试",
      "depends_on": ["t2"],
      "tools_required": ["read_file", "write_file", "shell_exec"],
      "assigned_official": "bingbu",
      "can_run_parallel": false,
      "estimated_tokens": 3000
    },
    {
      "task_id": "t4",
      "description": "更新 API 文档",
      "depends_on": ["t2"],
      "tools_required": ["read_file", "write_file"],
      "assigned_official": "wenyuan",
      "can_run_parallel": true,
      "estimated_tokens": 1500
    }
  ],
  "priority_order": ["t1", "t2", "t3", "t4"]
}
"""


def build_planning_prompt(
    persona_context: str,
    officials_roster: str,
    tools_list: str,
) -> str:
    """Assemble the full planning system prompt from dynamic components."""
    parts: list[str] = []

    if persona_context:
        parts.append(persona_context)

    parts.append(_PLANNING_ROLE)

    if officials_roster:
        parts.append(officials_roster)

    if tools_list:
        parts.append(tools_list)

    return "\n\n".join(parts)


def format_officials_roster(personas: list[AgentPersona]) -> str:
    """Format executor personas as a Markdown table for the LLM."""
    if not personas:
        return ""

    lines = [
        "# 可用执行官",
        "",
        "| ID | 名称 | 部门 | 可用工具 | 可委派至 |",
        "|----|------|------|----------|----------|",
    ]
    for p in personas:
        tools_str = ", ".join(p.tools_allowed) if p.tools_allowed else "（默认工具）"
        delegates_str = ", ".join(p.delegates_to) if p.can_delegate and p.delegates_to else "—"
        lines.append(f"| {p.id} | {p.name} | {p.department} | {tools_str} | {delegates_str} |")

    lines.append("")
    lines.append("assigned_official 必须从上表 ID 中选取。")
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
