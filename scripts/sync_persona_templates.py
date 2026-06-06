#!/usr/bin/env python3
"""Vendor persona role templates from the agency-agents repos into this repo.

Pulls Chinese templates from ``jnMetaCode/agency-agents-zh`` and English
templates from the upstream ``msitarzewski/agency-agents``, copying each
category's ``*.md`` agent files into::

    templates/persona/zh/{category}/*.md
    templates/persona/en/{category}/*.md

Both repos are MIT licensed. Re-run this script to refresh from upstream;
``templates/persona/SOURCES.md`` records the synced commit of each source.

Usage:
    python scripts/sync_persona_templates.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEST_ROOT = REPO_ROOT / "templates" / "persona"

# (lang, git url)
SOURCES = [
    ("zh", "https://github.com/jnMetaCode/agency-agents-zh.git"),
    ("en", "https://github.com/msitarzewski/agency-agents.git"),
]

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


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        cmd, cwd=cwd, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _is_category_dir(d: Path) -> bool:
    if not d.is_dir() or d.name in SKIP_DIRS:
        return False
    # A category dir holds at least one agent markdown file.
    return any(
        f.suffix == ".md" and not f.name.lower().startswith("readme")
        for f in d.iterdir()
    )


def sync_repo(lang: str, url: str, tmp: Path) -> tuple[int, int, str]:
    """Clone one repo and copy its category markdown into the dest tree.

    Returns (categories, files, commit_sha).
    """
    clone_dir = tmp / lang
    print(f"[{lang}] cloning {url} …")
    _run(["git", "clone", "--depth", "1", url, str(clone_dir)])
    commit = _run(["git", "rev-parse", "HEAD"], cwd=clone_dir)

    lang_dest = DEST_ROOT / lang
    if lang_dest.exists():
        shutil.rmtree(lang_dest)  # clean slate so removed-upstream files vanish

    n_cat = 0
    n_file = 0
    for entry in sorted(clone_dir.iterdir()):
        if not _is_category_dir(entry):
            continue
        n_cat += 1
        cat_dest = lang_dest / entry.name
        cat_dest.mkdir(parents=True, exist_ok=True)
        for md in sorted(entry.glob("*.md")):
            if md.name.lower().startswith("readme"):
                continue
            shutil.copy2(md, cat_dest / md.name)
            n_file += 1
    print(f"[{lang}] copied {n_file} templates across {n_cat} categories")
    return n_cat, n_file, commit


def write_sources(stats: dict[str, dict]) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
    for lang, url in SOURCES:
        s = stats[lang]
        repo = url.removesuffix(".git")
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
        for lang, url in SOURCES:
            try:
                cats, files, commit = sync_repo(lang, url, tmp)
            except subprocess.CalledProcessError as exc:
                print(f"[{lang}] FAILED: {exc.stderr or exc}", file=sys.stderr)
                return 1
            stats[lang] = {"categories": cats, "files": files, "commit": commit}
    write_sources(stats)
    total = sum(s["files"] for s in stats.values())
    print(f"\nDone. {total} templates vendored into {DEST_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
