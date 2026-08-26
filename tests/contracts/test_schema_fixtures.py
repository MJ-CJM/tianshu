from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pydantic
import pytest
from jsonschema import Draft202012Validator
from scripts.export_schemas import SCHEMA_EXPORTS

_ROOT = Path(__file__).parents[2]
_SCHEMA_ROOT = _ROOT / "docs" / "reference"
_EXPECTED_FILENAMES = (
    "evidence-bundle-v1.schema.json",
    "lean-preview-demo-report-v1.schema.json",
    "lean-preview-candidate-report-v1.schema.json",
    "executor-capability-manifest-v1.schema.json",
    "effective-governance-contract-v1.schema.json",
    "agent-continuation-v1.schema.json",
    "outer-loop-continuation-v1.schema.json",
    "run-assignment-v1.schema.json",
    "system-snapshot-v1.schema.json",
    "runtime-release-v1.schema.json",
    "runtime-generation-v1.schema.json",
    "evolution-policy-v1.schema.json",
)


def test_schema_exports_cover_the_frozen_v1_contracts() -> None:
    assert tuple(filename for filename, _exporter in SCHEMA_EXPORTS) == _EXPECTED_FILENAMES


@pytest.mark.parametrize(
    ("filename", "exporter"),
    SCHEMA_EXPORTS,
    ids=[filename for filename, _exporter in SCHEMA_EXPORTS],
)
def test_checked_in_schema_matches_registered_exporter(filename, exporter) -> None:
    expected = exporter()
    actual = json.loads((_SCHEMA_ROOT / filename).read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(expected)
    assert expected["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert expected["$id"] == f"https://tianshu.dev/schemas/{filename}"
    assert expected["x-pydantic-version"] == pydantic.__version__
    assert expected["additionalProperties"] is False
    assert actual == expected


def test_pydantic_is_pinned_to_the_schema_generator_minor() -> None:
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    major, minor, *_rest = pydantic.__version__.split(".")

    assert f"pydantic>={major}.{minor},<{major}.{int(minor) + 1}" in project["dependencies"]
