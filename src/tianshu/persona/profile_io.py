"""PROFILE.md I/O utilities — atomic write + history prune."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

HISTORY_KEEP = 10
HISTORY_NAME_RE = re.compile(r"^v(\d+)-\d{4}-\d{2}-\d{2}\.md$")


def atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via tmp + rename (POSIX)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def archive_previous(profile_path: Path, version: int) -> Path | None:
    """Move current PROFILE.md into profile_history/ before overwriting."""
    if not profile_path.exists():
        return None
    history_dir = profile_path.parent / "profile_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    archive_path = history_dir / f"v{version}-{today}.md"
    archive_path.write_text(profile_path.read_text(encoding="utf-8"), encoding="utf-8")
    return archive_path


def prune_history(history_dir: Path, keep: int = HISTORY_KEEP) -> list[Path]:
    """Remove oldest archives beyond `keep`. Returns pruned paths."""
    if not history_dir.exists():
        return []
    versions: list[tuple[int, Path]] = []
    for f in history_dir.iterdir():
        m = HISTORY_NAME_RE.match(f.name)
        if m:
            versions.append((int(m.group(1)), f))
    versions.sort(key=lambda t: t[0], reverse=True)
    pruned: list[Path] = []
    for _, f in versions[keep:]:
        try:
            f.unlink()
            pruned.append(f)
        except OSError as e:
            logger.warning("Failed to prune %s: %s", f, e)
    return pruned


def quarantine_corrupted(profile_path: Path) -> Path | None:
    """Move a corrupted PROFILE.md into profile_history/corrupted/ and return new path."""
    if not profile_path.exists():
        return None
    corrupted_dir = profile_path.parent / "profile_history" / "corrupted"
    corrupted_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")
    dst = corrupted_dir / f"PROFILE-{ts}.md"
    profile_path.rename(dst)
    return dst
