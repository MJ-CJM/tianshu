#!/usr/bin/env python3
"""Vendor persona role templates from the agency-agents repos into this repo.

Downloads pinned GitHub archives for ``jnMetaCode/agency-agents-zh`` and
``msitarzewski/agency-agents``, copying each
category's ``*.md`` agent files into::

    templates/persona/zh/{category}/*.md
    templates/persona/en/{category}/*.md

Both repos are MIT licensed. Re-run this script to refresh from upstream;
``templates/persona/SOURCES.md`` records the synced commit of each source.

Usage:
    python scripts/sync_persona_templates.py
"""

from __future__ import annotations

import io
import json
import re
import shutil
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

REPO_ROOT = Path(__file__).resolve().parent.parent
DEST_ROOT = REPO_ROOT / "src" / "tianshu" / "resources" / "persona_templates"

# (lang, GitHub owner, repository)
SOURCES = [
    ("zh", "jnMetaCode", "agency-agents-zh"),
    ("en", "msitarzewski", "agency-agents"),
]

_GITHUB_SLUG_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
_MAX_API_BYTES = 1_000_000
_MAX_ARCHIVE_BYTES = 50_000_000
_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_UNPACKED_BYTES = 250_000_000
_MAX_TEMPLATE_BYTES = 2_000_000

# Top-level dirs that are NOT role categories.
SKIP_DIRS = {
    ".git",
    ".github",
    "assets",
    "examples",
    "integrations",
    "scripts",
    "node_modules",
}


def _validate_github_slug(value: str) -> str:
    if not _GITHUB_SLUG_RE.fullmatch(value):
        raise ValueError(f"invalid GitHub repository component: {value!r}")
    return value


def _validate_https_endpoint(url: str, allowed_hosts: frozenset[str]) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"untrusted archive endpoint: {url!r}") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise ValueError(f"untrusted archive endpoint: {url!r}")


class _AllowedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: frozenset[str]) -> None:
        self._allowed_hosts = allowed_hosts
        super().__init__()

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        _validate_https_endpoint(newurl, self._allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_https(request: Request, allowed_hosts: frozenset[str]) -> Any:
    opener = build_opener(ProxyHandler({}), _AllowedRedirectHandler(allowed_hosts))
    return opener.open(request, timeout=30)


def _fetch_https(url: str, *, allowed_hosts: frozenset[str], max_bytes: int) -> bytes:
    _validate_https_endpoint(url, allowed_hosts)
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "tianshu-persona-template-sync/1",
        },
    )
    with _open_https(request, allowed_hosts) as response:
        _validate_https_endpoint(response.geturl(), allowed_hosts)
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"download exceeds {max_bytes} byte limit")
    return data


def _resolve_github_commit(owner: str, repository: str) -> str:
    owner = _validate_github_slug(owner)
    repository = _validate_github_slug(repository)
    payload = _fetch_https(
        f"https://api.github.com/repos/{owner}/{repository}/commits/HEAD",
        allowed_hosts=frozenset({"api.github.com"}),
        max_bytes=_MAX_API_BYTES,
    )
    try:
        commit = json.loads(payload)["sha"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("GitHub returned malformed commit metadata") from exc
    if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
        raise ValueError("GitHub returned an invalid commit SHA")
    return commit


def _download_github_archive(owner: str, repository: str, commit: str) -> bytes:
    owner = _validate_github_slug(owner)
    repository = _validate_github_slug(repository)
    if not _COMMIT_RE.fullmatch(commit):
        raise ValueError("invalid pinned commit SHA")
    return _fetch_https(
        f"https://codeload.github.com/{owner}/{repository}/tar.gz/{commit}",
        allowed_hosts=frozenset({"codeload.github.com"}),
        max_bytes=_MAX_ARCHIVE_BYTES,
    )


def _safe_archive_parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    parts = path.parts
    if (
        not parts
        or path.is_absolute()
        or "\\" in name
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError(f"unsafe archive path: {name!r}")
    return parts


def _materialize_templates(archive_data: bytes, destination: Path) -> tuple[int, int]:
    destination = Path(destination)
    if destination.exists():
        raise ValueError(f"archive destination already exists: {destination}")
    destination.mkdir(parents=True)
    categories: set[str] = set()
    written: set[Path] = set()
    archive_root: str | None = None
    member_count = 0
    unpacked_bytes = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:gz") as archive:
            for member in archive:
                member_count += 1
                if member_count > _MAX_ARCHIVE_MEMBERS:
                    raise ValueError("archive contains too many entries")
                if member.size < 0:
                    raise ValueError("archive contains a negative-size entry")
                unpacked_bytes += member.size
                if unpacked_bytes > _MAX_UNPACKED_BYTES:
                    raise ValueError("archive exceeds unpacked byte limit")
                parts = _safe_archive_parts(member.name)
                if archive_root is None:
                    archive_root = parts[0]
                elif parts[0] != archive_root:
                    raise ValueError("archive contains multiple top-level roots")
                if len(parts) != 3:
                    continue
                _root, category, filename = parts
                if (
                    category in SKIP_DIRS
                    or not filename.lower().endswith(".md")
                    or filename.lower().startswith("readme")
                ):
                    continue
                if not member.isfile():
                    raise ValueError(f"template entry is not a regular file: {member.name!r}")
                if member.size > _MAX_TEMPLATE_BYTES:
                    raise ValueError(f"template exceeds {_MAX_TEMPLATE_BYTES} byte limit")
                target = destination / category / filename
                if target in written:
                    raise ValueError(f"duplicate template archive path: {member.name!r}")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"template entry cannot be read: {member.name!r}")
                content = source.read(_MAX_TEMPLATE_BYTES + 1)
                if len(content) != member.size:
                    raise ValueError(f"template size mismatch: {member.name!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                written.add(target)
                categories.add(category)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return len(categories), len(written)


def sync_repo(
    lang: str,
    owner: str,
    repository: str,
    tmp: Path,
) -> tuple[int, int, str]:
    """Download one pinned repo archive and copy its category markdown.

    Returns (categories, files, commit_sha).
    """
    print(f"[{lang}] resolving {owner}/{repository} …")
    commit = _resolve_github_commit(owner, repository)
    archive = _download_github_archive(owner, repository, commit)
    staging = tmp / lang
    n_cat, n_file = _materialize_templates(archive, staging)

    DEST_ROOT.mkdir(parents=True, exist_ok=True)
    lang_dest = DEST_ROOT / lang
    if lang_dest.exists():
        shutil.rmtree(lang_dest)  # clean slate so removed-upstream files vanish
    shutil.move(str(staging), lang_dest)
    print(f"[{lang}] copied {n_file} templates across {n_cat} categories")
    return n_cat, n_file, commit


def write_sources(stats: dict[str, dict]) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    lines = [
        "# Persona Template Sources",
        "",
        "Vendored by `scripts/sync_persona_templates.py`. Both sources are MIT licensed.",
        "",
        f"Last synced: {now}",
        "",
        "| Lang | Repo | Commit | Categories | Templates |",
        "|------|------|--------|-----------:|----------:|",
    ]
    for lang, owner, repository in SOURCES:
        s = stats[lang]
        repo = f"https://github.com/{owner}/{repository}"
        lines.append(
            f"| {lang} | [{repo.split('github.com/')[-1]}]({repo}) "
            f"| `{s['commit'][:12]}` | {s['categories']} | {s['files']} |"
        )
    lines.append("")
    (DEST_ROOT / "SOURCES.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    DEST_ROOT.mkdir(parents=True, exist_ok=True)
    stats: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="agency-agents-") as tmpdir:
        tmp = Path(tmpdir)
        for lang, owner, repository in SOURCES:
            try:
                cats, files, commit = sync_repo(lang, owner, repository, tmp)
            except (OSError, ValueError, tarfile.TarError) as exc:
                print(f"[{lang}] FAILED: {exc}", file=sys.stderr)
                return 1
            stats[lang] = {"categories": cats, "files": files, "commit": commit}
    write_sources(stats)
    total = sum(s["files"] for s in stats.values())
    print(f"\nDone. {total} templates vendored into {DEST_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
