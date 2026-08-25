from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from tianshu.models.canonical import canonical_sha256
from tianshu.models.system_snapshot import SystemSnapshotV1


def _snapshot(components: dict[str, str]) -> SystemSnapshotV1:
    return SystemSnapshotV1(
        components=components,
        digest=canonical_sha256(components),
    )


def test_snapshot_is_order_independent_deeply_immutable_and_json_round_trips() -> None:
    forward = {"kernel": "0" * 64, "skills": "1" * 64}
    reverse = dict(reversed(list(forward.items())))

    snapshot = _snapshot(forward)
    assert snapshot.digest == _snapshot(reverse).digest
    assert json.loads(snapshot.model_dump_json())["components"] == forward
    assert SystemSnapshotV1.model_validate_json(snapshot.model_dump_json()) == snapshot
    assert snapshot.model_copy(deep=True) == snapshot
    assert SystemSnapshotV1.model_json_schema()["properties"]["components"]["type"] == "object"

    forward["kernel"] = "2" * 64
    assert snapshot.components["kernel"] == "0" * 64
    with pytest.raises(TypeError, match="immutable"):
        snapshot.components["kernel"] = "2" * 64
    with pytest.raises(TypeError, match="immutable"):
        snapshot.components.update({"skills": "2" * 64})


@pytest.mark.parametrize(
    ("components", "digest", "match"),
    [
        ({"unknown": "0" * 64}, None, "unsupported"),
        ({"executor:": "0" * 64}, None, "unsupported"),
        ({"kernel": "A" * 64}, None, "lowercase SHA-256"),
        ({"kernel": "0" * 64}, "f" * 64, "does not match"),
    ],
)
def test_snapshot_rejects_invalid_content(
    components: dict[str, str],
    digest: str | None,
    match: str,
) -> None:
    with pytest.raises(ValidationError, match=match):
        SystemSnapshotV1(
            components=components,
            digest=digest or canonical_sha256(components),
        )


def test_snapshot_rejects_more_than_64_components() -> None:
    components = {f"executor:adapter-{index}": "0" * 64 for index in range(65)}

    with pytest.raises(ValidationError, match="at most 64"):
        _snapshot(components)
