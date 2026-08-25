from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ._fixtures import evidence_service, seed_closed_run


def _schema() -> dict[str, object]:
    return json.loads(
        (
            Path(__file__).parents[2] / "docs" / "reference" / "evidence-bundle-v1.schema.json"
        ).read_text()
    )


def _closed_payload(storage, tmp_path) -> dict[str, object]:
    _, memorial = seed_closed_run(storage)
    service = evidence_service(storage, tmp_path / "artifacts")
    opened = service.build_open(memorial.id)
    closed = service.close(memorial.id, expected_version=opened.version)
    return json.loads(service.export(closed.bundle_id))


@pytest.mark.parametrize(
    "field",
    [
        "requested_contract",
        "effective_contract",
        "executor_manifest",
        "plan_revision",
    ],
)
@pytest.mark.parametrize("mutation", ["empty", "missing", "additional"])
def test_published_schema_rejects_invalid_nested_contracts(
    storage,
    tmp_path,
    field: str,
    mutation: str,
) -> None:
    payload = _closed_payload(storage, tmp_path)
    nested = payload["snapshot"][field]
    if mutation == "empty":
        payload["snapshot"][field] = {}
    elif mutation == "missing":
        required_key = {
            "requested_contract": "objective",
            "effective_contract": "objective",
            "executor_manifest": "manifest_id",
            "plan_revision": "revision_id",
        }[field]
        del nested[required_key]
    else:
        nested["unexpected"] = True

    errors = list(Draft202012Validator(_schema()).iter_errors(copy.deepcopy(payload)))

    assert errors
