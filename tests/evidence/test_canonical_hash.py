from __future__ import annotations

import hashlib
import json

from tianshu.evidence.models import closed_bundle_content_hash
from tianshu.models.canonical import canonical_json_bytes


def test_closed_hash_excludes_only_content_hash_and_is_mapping_order_stable() -> None:
    payload = {
        "schema_version": "1.0",
        "bundle_id": "bundle-1",
        "content_hash": "0" * 64,
        "nested": {"z": None, "a": [2, 1]},
    }
    reordered = json.loads(
        '{"nested":{"a":[2,1],"z":null},"content_hash":"'
        + "f" * 64
        + '","bundle_id":"bundle-1","schema_version":"1.0"}'
    )

    expected = hashlib.sha256(
        canonical_json_bytes(
            {key: value for key, value in payload.items() if key != "content_hash"}
        )
    ).hexdigest()
    assert closed_bundle_content_hash(payload) == expected
    assert closed_bundle_content_hash(reordered) == expected

    changed = {**payload, "bundle_id": "bundle-2"}
    assert closed_bundle_content_hash(changed) != expected
