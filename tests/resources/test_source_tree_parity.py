"""S1.1 过渡期守卫：repo 根资源树与包内 canonical 树逐文件字节一致。

S1.1 以复制方式建立包内 canonical 树（`src/tianshu/resources/`），根树
（`personas/`、`templates/persona/`、`LICENSE`）在消费者切换（S1.2）前保留。
本守卫使任何单边漂移立刻变红；S1.2 删除根树时应同步删除本文件。
"""

import hashlib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_RESOURCES = _REPO_ROOT / "src" / "tianshu" / "resources"


def _tree_digests(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


@pytest.mark.parametrize(
    ("repo_tree", "packaged_tree"),
    [
        ("personas", "personas"),
        ("templates/persona", "persona_templates"),
    ],
)
def test_repo_tree_matches_packaged_canonical_tree(repo_tree: str, packaged_tree: str) -> None:
    repo = _tree_digests(_REPO_ROOT / repo_tree)
    packaged = _tree_digests(_PACKAGE_RESOURCES / packaged_tree)
    assert repo == packaged


def test_repo_license_matches_packaged_license() -> None:
    repo = hashlib.sha256((_REPO_ROOT / "LICENSE").read_bytes()).hexdigest()
    packaged = hashlib.sha256((_PACKAGE_RESOURCES / "LICENSE").read_bytes()).hexdigest()
    assert repo == packaged
