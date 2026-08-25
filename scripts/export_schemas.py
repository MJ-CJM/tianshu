"""Regenerate the checked-in V1 contract JSON Schema fixtures."""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import partial
from pathlib import Path

from pydantic import BaseModel

from tianshu.evidence.models import ClosedEvidenceBundleV1
from tianshu.executor.capabilities import ExecutorCapabilityManifestV1
from tianshu.models.governance_contract import EffectiveGovernanceContractV1
from tianshu.models.lean_preview import (
    lean_preview_candidate_report_schema,
    lean_preview_demo_report_schema,
)
from tianshu.models.run_assignment import RunAssignmentV1
from tianshu.models.run_state import AgentContinuationV1, OuterLoopContinuationV1
from tianshu.models.schema_export import schema_for
from tianshu.models.system_snapshot import SystemSnapshotV1

type SchemaExporter = Callable[[], dict[str, object]]

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_ROOT = _ROOT / "docs" / "reference"


def _model_export(
    filename: str,
    model: type[BaseModel],
) -> tuple[str, SchemaExporter]:
    return filename, partial(schema_for, model, filename)


SCHEMA_EXPORTS: tuple[tuple[str, SchemaExporter], ...] = (
    _model_export("evidence-bundle-v1.schema.json", ClosedEvidenceBundleV1),
    ("lean-preview-demo-report-v1.schema.json", lean_preview_demo_report_schema),
    ("lean-preview-candidate-report-v1.schema.json", lean_preview_candidate_report_schema),
    _model_export(
        "executor-capability-manifest-v1.schema.json",
        ExecutorCapabilityManifestV1,
    ),
    _model_export(
        "effective-governance-contract-v1.schema.json",
        EffectiveGovernanceContractV1,
    ),
    _model_export("agent-continuation-v1.schema.json", AgentContinuationV1),
    _model_export("outer-loop-continuation-v1.schema.json", OuterLoopContinuationV1),
    _model_export("run-assignment-v1.schema.json", RunAssignmentV1),
    _model_export("system-snapshot-v1.schema.json", SystemSnapshotV1),
)


def export_schemas() -> None:
    for filename, exporter in SCHEMA_EXPORTS:
        rendered = json.dumps(exporter(), ensure_ascii=False, indent=2) + "\n"
        (_SCHEMA_ROOT / filename).write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    export_schemas()
