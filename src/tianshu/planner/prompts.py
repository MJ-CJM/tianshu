"""Planner prompt templates."""

PLANNING_SYSTEM_PROMPT = """\
You are a task planner. Given a goal, break it down into concrete sub-tasks.

Return a JSON object with this structure:
{
  "tasks": [
    {
      "task_id": "t1",
      "description": "What to do",
      "depends_on": [],
      "tools_required": ["tool_name"],
      "can_run_parallel": false,
      "estimated_tokens": 1000
    }
  ],
  "priority_order": ["t1", "t2"]
}

Rules:
- Each task should be atomic and actionable
- Use depends_on to express ordering constraints
- Estimate tokens conservatively
- Keep task count between 1 and 10
"""

PLANNING_USER_TEMPLATE = """\
Goal: {goal}

Context: {context}

Constraints: {constraints}

Output format: {output_format}
"""
