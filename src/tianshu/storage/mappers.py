"""Storage 行映射（row -> model/dict）—— 从 facade 的 staticmethod 抽出为模块级纯函数。"""

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any

from tianshu.models import (
    AuditResult,
    DAGExecution,
    DAGNode,
    DAGNodeStatus,
    Edict,
    EdictDispatch,
    EdictRuntime,
    EdictSchedule,
    EdictStatus,
    Memorial,
    TaskStatus,
    UsageSummary,
)
from tianshu.models.acceptance import AcceptanceCriteria

logger = logging.getLogger(__name__)


def _load_json_field(raw: str, loader, field: str, entity_id: str, default):
    """反序列化行 JSON 字段；失败时 warning 并返回 default（容忍历史脏数据）。"""
    try:
        return loader(raw)
    except Exception as exc:
        logger.warning("Failed to deserialize %s for %s: %s", field, entity_id, exc)
        return default


_MemoryEntry = None


def _get_memory_entry():
    global _MemoryEntry
    if _MemoryEntry is None:
        from tianshu.memory.models import MemoryEntry

        _MemoryEntry = MemoryEntry
    return _MemoryEntry


def _row_to_memory_entry(row: sqlite3.Row):
    MemoryEntry = _get_memory_entry()
    return MemoryEntry(
        id=row["id"],
        persona_id=row["persona_id"],
        edict_id=row["edict_id"],
        memorial_id=row["memorial_id"],
        category=row["category"],
        content=row["content"],
        source=row["source"],
        confidence=row["confidence"],
        entity_refs=json.loads(row["entity_refs_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        access_level=row["access_level"],
    )


def _row_to_dag_execution(row: sqlite3.Row, nodes: list[DAGNode]) -> DAGExecution:
    return DAGExecution(
        id=row["id"],
        edict_id=row["edict_id"],
        plan_json=row["plan_json"],
        status=row["status"],
        root_memorial_id=row["root_memorial_id"],
        max_concurrency=row["max_concurrency"],
        created_at=datetime.fromisoformat(row["created_at"]),
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        nodes=nodes,
    )


def _row_to_dag_node(row: sqlite3.Row) -> DAGNode:
    return DAGNode(
        node_id=row["node_id"],
        dag_execution_id=row["dag_execution_id"],
        description=row["description"],
        depends_on=json.loads(row["depends_on_json"]),
        status=DAGNodeStatus(row["status"]),
        assigned_official=row["assigned_official"],
        assigned_worker=row["assigned_worker"],
        tools_required=json.loads(row["tools_required_json"]),
        memorial_id=row["memorial_id"],
        checkpoint_json=row["checkpoint_json"],
        started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
        completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        error=row["error"],
    )


def _row_to_persona_dict(row: sqlite3.Row) -> dict:
    keys = row.keys()
    return {
        "id": row["id"],
        "name": row["name"],
        "department": row["department"],
        "title": row["title"] if "title" in keys else None,
        "tools_allowed": json.loads(row["tools_allowed"]),
        "tools_denied": json.loads(row["tools_denied"]),
        "skills_allowed": json.loads(row["skills_allowed"]) if "skills_allowed" in keys else [],
        "tool_tier_max": row["tool_tier_max"],
        "can_delegate": bool(row["can_delegate"]),
        "memory_global_read": bool(row["memory_global_read"])
        if "memory_global_read" in keys
        else False,
        "delegates_to": json.loads(row["delegates_to"]),
        "soul_path": row["soul_path"],
        "role_path": row["role_path"],
        "llm_config_name": row["llm_config_name"] if "llm_config_name" in keys else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_eval_run(row) -> dict:
    return {
        "id": row["id"],
        "universe_id": row["universe_id"],
        "gate_passed": bool(row["gate_passed"]),
        "gate_detail": json.loads(row["gate_detail"]) if row["gate_detail"] else None,
        "fitness": json.loads(row["fitness_json"] or "{}"),
        "baseline": json.loads(row["baseline_json"]) if row["baseline_json"] else None,
        "eval_set_version": row["eval_set_version"],
        "cost": row["cost"],
        "created_at": row["created_at"],
    }


def _row_to_universe(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "parent_universe_id": row["parent_universe_id"],
        "status": row["status"],
        "origin": row["origin"],
        "mutation_reason": row["mutation_reason"],
        "description": row["description"],
        "fitness": json.loads(row["fitness_json"] or "{}"),
        "code_ref": row["code_ref"],
        "created_at": row["created_at"],
    }


def _row_to_edict(row: sqlite3.Row) -> Edict:
    # Handle optional Phase 1 columns gracefully
    keys = row.keys()

    schedule = EdictSchedule()
    if "schedule_json" in keys and row["schedule_json"]:
        schedule = _load_json_field(
            row["schedule_json"],
            EdictSchedule.model_validate_json,
            "schedule_json",
            row["id"],
            schedule,
        )

    dispatch = None
    if "dispatch_json" in keys and row["dispatch_json"]:
        dispatch = _load_json_field(
            row["dispatch_json"],
            EdictDispatch.model_validate_json,
            "dispatch_json",
            row["id"],
            dispatch,
        )

    runtime = EdictRuntime()
    if "runtime_json" in keys and row["runtime_json"]:
        runtime = _load_json_field(
            row["runtime_json"],
            EdictRuntime.model_validate_json,
            "runtime_json",
            row["id"],
            runtime,
        )

    constraints: list[str] = []
    if "constraints_json" in keys and row["constraints_json"]:
        constraints = _load_json_field(
            row["constraints_json"],
            json.loads,
            "constraints_json",
            row["id"],
            constraints,
        )

    metadata: dict[str, Any] = {}
    if "metadata_json" in keys and row["metadata_json"]:
        metadata = _load_json_field(
            row["metadata_json"],
            json.loads,
            "metadata_json",
            row["id"],
            metadata,
        )

    acceptance = None
    if "acceptance_json" in keys and row["acceptance_json"]:
        acceptance = _load_json_field(
            row["acceptance_json"],
            AcceptanceCriteria.model_validate_json,
            "acceptance_json",
            row["id"],
            acceptance,
        )

    return Edict(
        id=row["id"],
        title=row["title"] if "title" in keys else "",
        goal=row["goal"],
        context=row["context"],
        status=EdictStatus(row["status"]) if "status" in keys else EdictStatus.OPEN,
        created_at=datetime.fromisoformat(row["created_at"]),
        idempotency_key=row["idempotency_key"] if "idempotency_key" in keys else None,
        source=row["source"] if "source" in keys else "api",
        submitter=row["submitter"] if "submitter" in keys else None,
        priority=row["priority"] if "priority" in keys else "normal",
        review_policy=row["review_policy"] if "review_policy" in keys else "never",
        output_format=row["output_format"] if "output_format" in keys else None,
        assigned_persona_id=row["assigned_persona_id"] if "assigned_persona_id" in keys else None,
        planner_persona_id=row["planner_persona_id"] if "planner_persona_id" in keys else None,
        plan_review=bool(row["plan_review"]) if "plan_review" in keys else False,
        acceptance=acceptance,
        execution_profile=row["execution_profile"] if "execution_profile" in keys else "foreground",
        constraints=constraints,
        schedule=schedule,
        dispatch=dispatch,
        runtime=runtime,
        metadata=metadata,
    )


def _row_to_memorial(row: sqlite3.Row) -> Memorial:
    keys = row.keys()
    usage_data = json.loads(row["usage_json"]) if row["usage_json"] else {}

    audit = None
    if "audit_json" in keys and row["audit_json"]:
        audit = _load_json_field(
            row["audit_json"],
            AuditResult.model_validate_json,
            "audit_json",
            row["id"],
            audit,
        )

    return Memorial(
        id=row["id"],
        edict_id=row["edict_id"],
        instruction=row["instruction"] if "instruction" in keys else None,
        status=TaskStatus(row["status"]),
        summary=row["summary"],
        result=row["result"],
        usage=UsageSummary(**usage_data),
        error=row["error"],
        created_at=datetime.fromisoformat(row["created_at"]),
        last_heartbeat_at=(
            datetime.fromisoformat(row["last_heartbeat_at"])
            if "last_heartbeat_at" in keys and row["last_heartbeat_at"]
            else None
        ),
        started_at=(datetime.fromisoformat(row["started_at"]) if row["started_at"] else None),
        completed_at=(datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None),
        attempt=row["attempt"] if "attempt" in keys else 1,
        parent_memorial_id=row["parent_memorial_id"] if "parent_memorial_id" in keys else None,
        review_status=row["review_status"] if "review_status" in keys else "not_required",
        audit=audit,
        dag_node_id=row["dag_node_id"] if "dag_node_id" in keys else None,
        persona_id=row["persona_id"] if "persona_id" in keys else None,
        runtime_override=_parse_runtime_override(row, keys),
        acceptance_override=_parse_acceptance_override(row, keys),
        reasoning_content=(row["reasoning_content"] if "reasoning_content" in keys else None),
        final_output=(row["final_output"] if "final_output" in keys else None),
        universe_id=row["universe_id"] if "universe_id" in keys else None,
        feedback_score=row["feedback_score"] if "feedback_score" in keys else 0,
    )


def _parse_runtime_override(row: sqlite3.Row, keys) -> dict | None:
    if "runtime_override_json" not in keys or not row["runtime_override_json"]:
        return None
    try:
        data = json.loads(row["runtime_override_json"])
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("invalid runtime_override_json for memorial %s", row["id"])
        return None


def _parse_acceptance_override(row: sqlite3.Row, keys) -> "AcceptanceCriteria | None":
    if "acceptance_override_json" not in keys or not row["acceptance_override_json"]:
        return None
    try:
        return AcceptanceCriteria.model_validate_json(row["acceptance_override_json"])
    except Exception:
        logger.warning("invalid acceptance_override_json for memorial %s", row["id"])
        return None


def _memorial_to_params(m: Memorial) -> tuple:
    return (
        m.id,
        m.edict_id,
        m.instruction,
        m.status.value,
        m.summary,
        m.result,
        m.usage.model_dump_json(),
        m.error,
        m.created_at.isoformat(),
        m.started_at.isoformat() if m.started_at else None,
        m.completed_at.isoformat() if m.completed_at else None,
        m.attempt,
        m.parent_memorial_id,
        m.review_status,
        m.audit.model_dump_json() if m.audit else None,
        json.dumps([a.model_dump() for a in m.artifacts], default=str),
        json.dumps([t.model_dump() for t in m.timeline], default=str),
        m.dag_node_id,
        m.persona_id,
        json.dumps(m.runtime_override) if m.runtime_override else None,
        m.acceptance_override.model_dump_json() if m.acceptance_override else None,
        m.reasoning_content,
        m.final_output,
        m.universe_id,
        m.last_heartbeat_at.isoformat() if m.last_heartbeat_at else None,
    )
