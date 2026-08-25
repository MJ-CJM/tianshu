"""Shared JSON Schema generation for checked-in V1 contract fixtures."""

from __future__ import annotations

import pydantic
from pydantic import BaseModel

_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
_SCHEMA_BASE = "https://tianshu.dev/schemas"


def schema_for(model: type[BaseModel], filename: str) -> dict[str, object]:
    """Build one serialization schema with the repository fixture identity."""

    schema = model.model_json_schema(mode="serialization")
    schema["$schema"] = _DRAFT_2020_12
    schema["$id"] = f"{_SCHEMA_BASE}/{filename}"
    schema["x-pydantic-version"] = pydantic.__version__
    return schema


__all__ = ["schema_for"]
