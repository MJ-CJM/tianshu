"""Shared canonical JSON bytes and SHA-256 helpers."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class RedactedError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    retryable: bool
    details_hash: str | None


def _require_string_mapping_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON mapping keys must be strings")
            _require_string_mapping_keys(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _require_string_mapping_keys(nested)


def _normalize_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON mapping keys must be strings")
            normalized[key] = _normalize_json_value(nested)
        return normalized
    if isinstance(value, list):
        return [_normalize_json_value(nested) for nested in value]
    raise TypeError(f"canonical JSON value is not JSON-compatible: {type(value).__name__}")


def canonical_json_bytes(value: BaseModel | Mapping[str, object]) -> bytes:
    """Serialize a model or mapping to strict deterministic UTF-8 JSON."""

    payload: object
    if isinstance(value, BaseModel):
        _require_string_mapping_keys(value.model_dump(mode="python"))
        payload = value.model_dump(mode="json", exclude_none=False)
    else:
        payload = value
    normalized = _normalize_json_value(payload)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: BaseModel | Mapping[str, object]) -> str:
    """Return the lowercase SHA-256 of the canonical JSON bytes."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


__all__ = [
    "JsonScalar",
    "JsonValue",
    "RedactedError",
    "canonical_json_bytes",
    "canonical_sha256",
]
