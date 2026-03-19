"""Plan model — task decomposition from the Planner."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlanTask(BaseModel):
    task_id: str
    description: str
    depends_on: list[str] = Field(default_factory=list)
    tools_required: list[str] = Field(default_factory=list)
    can_run_parallel: bool = False
    estimated_tokens: int | None = None
    assigned_official: str | None = None


class Plan(BaseModel):
    tasks: list[PlanTask] = Field(default_factory=list)
    priority_order: list[str] = Field(default_factory=list)
    total_estimated_tokens: int | None = None

    def to_dag(self, edict_id: str, max_concurrency: int = 1) -> object:
        """Convert this Plan into a DAGExecution.

        Single-task plans produce a single-node DAG for backward compatibility.
        """
        from tianshu.dag.models import DAGExecution, DAGNode

        nodes = [
            DAGNode(
                node_id=task.task_id,
                description=task.description,
                depends_on=list(task.depends_on),
                assigned_official=task.assigned_official,
                tools_required=list(task.tools_required),
            )
            for task in self.tasks
        ]

        return DAGExecution(
            edict_id=edict_id,
            plan_json=self.model_dump_json(),
            max_concurrency=max_concurrency,
            nodes=nodes,
        )
