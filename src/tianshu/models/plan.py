"""Plan model — task decomposition from the Planner."""

from __future__ import annotations

from typing import Literal

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
    planning_mode: Literal["generated", "direct", "fallback"] = "generated"
    fallback_reason: str | None = None
    # 实际主持规划的官员（内阁决策模式下默认为内阁官员）；None = 无人格裸规划。
    # exclude=True：纯运行时观测字段，不进入 model_dump/to_dag/plan_hash——
    # durable plan 证据（RunState.plan_snapshot 与 plan_revisions.plan_hash）
    # 的序列化形状跨版本必须字节稳定，否则升级前在途的多任务运行恢复时
    # canonical hash 对不上会永久失败。对外透出走事件 payload 根部。
    planner_persona_id: str | None = Field(default=None, exclude=True)

    def to_dag(self, edict_id: str, max_concurrency: int = 1) -> object:
        """Convert this Plan into a DAGExecution.

        Single-task plans produce a single-node DAG for backward compatibility.
        """
        from tianshu.models.dag import DAGExecution, DAGNode

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
