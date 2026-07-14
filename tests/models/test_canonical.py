"""Shared canonical JSON and digest contracts."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

import pytest
from pydantic import BaseModel


def _canonical_json_bytes(value: BaseModel | Mapping[str, object]) -> bytes:
    from tianshu.models.canonical import canonical_json_bytes

    return canonical_json_bytes(value)


def _canonical_sha256(value: BaseModel | Mapping[str, object]) -> str:
    from tianshu.models.canonical import canonical_sha256

    return canonical_sha256(value)


class _Payload(BaseModel):
    zeta: str
    alpha: str | None = None
    nested: dict[str, object]


def test_canonical_json_is_sorted_utf8_and_keeps_explicit_nulls() -> None:
    payload = _Payload(
        zeta="天枢",
        alpha=None,
        nested={"present": True, "explicit_null": None},
    )

    assert (
        _canonical_json_bytes(payload)
        == ('{"alpha":null,"nested":{"explicit_null":null,"present":true},"zeta":"天枢"}').encode()
    )


def test_canonical_mapping_accepts_mapping_and_hashes_exact_bytes() -> None:
    payload = MappingProxyType({"z": [2, 1], "a": None})

    assert _canonical_json_bytes(payload) == b'{"a":null,"z":[2,1]}'
    assert _canonical_sha256(payload) == (
        "f71626226ccce300a5150091f2a470adb72ab1d6d1877608d5e51ca899a80848"
    )


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_canonical_json_rejects_non_finite_numbers(non_finite: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        _canonical_json_bytes({"nested": [non_finite]})


@pytest.mark.parametrize(
    "payload",
    [
        {1: "outer"},
        {"nested": {1: "inner"}},
    ],
)
def test_canonical_json_rejects_non_string_mapping_keys(payload: object) -> None:
    with pytest.raises(TypeError, match="mapping keys must be strings"):
        _canonical_json_bytes(payload)  # type: ignore[arg-type]


def test_canonical_json_does_not_coerce_unknown_values_with_default_str() -> None:
    class Stringifiable:
        def __str__(self) -> str:
            return "must-not-be-coerced"

    with pytest.raises(TypeError, match="JSON-compatible"):
        _canonical_json_bytes({"value": Stringifiable()})
