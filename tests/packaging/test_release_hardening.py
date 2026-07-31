"""Lightweight contracts for the public-source release surface."""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_ACTION_REF = re.compile(r"^\s*-\s+uses:\s+[^@\s]+@([0-9a-f]{40})(?:\s+#.*)?$")
_LOCAL_ONLY_IGNORE_SAMPLES = (
    ".env.local",
    ".tianshu-security.json",
    ".tianshu/tianshu.db-wal",
    ".claude/settings.local.json",
    ".claude/plans/local-plan.md",
    ".claude/skills/gstack/supabase/config.sh",
    ".claude/worktrees/task/.gitignore",
    ".codex/session.json",
    ".superpowers/sdd/local-report.md",
    ".mypy_cache/cache.json",
    ".import_linter_cache/graph",
    ".jbeval/results.json",
    "artifacts/g5/release/tianshu.whl",
    "web/.vite/deps/_metadata.json",
    "web/e2e/.auth/state.json",
    "web/playwright-report/index.html",
    "coverage.xml",
    "2025_hot_ai_directions.md",
    "README_scheduled_weather.md",
    "setup_cron.sh",
    "python-unit-test-guide.md",
    "=2.0",
)
_PUBLIC_SOURCE_SAMPLES = (
    ".env.example",
    "security/npm-audit-allowlist.json",
    "tests/packaging/test_release_hardening.py",
    "web/e2e/__screenshots__/golden.png",
)
_DOCKER_CONTEXT_EXCLUSIONS = {
    ".claude/",
    ".codex/",
    ".env.*",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".superpowers/",
    ".tianshu-security.json",
    ".tianshu/",
    "2025_hot_ai_directions.md",
    "=2.0",
    "AGENTS.md",
    "README_scheduled_weather.md",
    "artifacts/",
    "python-unit-test-design-guide.md",
    "python-unit-test-guide.md",
    "python-unittest-design-guide.md",
    "reports/",
    "sbom/",
    "set_permissions.sh",
    "setup_cron.sh",
    "setup_systemd_timer.sh",
    "test_weather.sh",
    "web/.vite/",
    "web/e2e/.auth/",
    "天气定时任务设置报告.md",
}
_FORBIDDEN_TRACKED_LOCAL_FILES = {
    ".claude/plans/moonlit-skipping-fog.md",
    ".claude/skills/gstack/supabase/config.sh",
    ".tianshu-security.json",
    "2025_hot_ai_directions.md",
    "=2.0",
    "README_scheduled_weather.md",
    "python-unit-test-design-guide.md",
    "python-unit-test-guide.md",
    "python-unittest-design-guide.md",
    "set_permissions.sh",
    "setup_cron.sh",
    "setup_systemd_timer.sh",
    "test_weather.sh",
    "web/.vite/deps/_metadata.json",
    "web/.vite/deps/package.json",
    "天气定时任务设置报告.md",
}


def test_project_metadata_is_self_describing_and_cli_is_installed_by_default() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["readme"] == {"file": "README.md", "content-type": "text/markdown"}
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"]
    assert project["authors"] == [{"name": "MJ-CJM"}]
    assert project["urls"]["Repository"] == "https://github.com/MJ-CJM/tianshu"
    assert "Operating System :: MacOS" in project["classifiers"]
    assert "Operating System :: POSIX :: Linux" in project["classifiers"]
    assert "Programming Language :: Python :: 3.12" in project["classifiers"]

    dependencies = project["dependencies"]
    assert any(value.startswith("typer") for value in dependencies)
    assert any(value.startswith("rich") for value in dependencies)
    assert any(value.startswith("websockets") for value in dependencies)


def test_release_engineering_files_exist() -> None:
    required = {
        "CODE_OF_CONDUCT.md",
        "LICENSE",
        "NOTICE",
        "THIRD_PARTY_NOTICES.md",
    }
    assert required <= {path.name for path in ROOT.iterdir() if path.is_file()}


def test_ci_uses_minimum_permissions_and_immutable_action_refs() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^permissions:\n  contents: read$", workflow)
    uses_lines = [line for line in workflow.splitlines() if re.match(r"^\s*-\s+uses:", line)]
    assert uses_lines
    assert all(_ACTION_REF.fullmatch(line) for line in uses_lines), uses_lines
    assert "actions/dependency-review-action@" in workflow


def test_legacy_docker_asset_has_a_real_runtime_and_non_root_user() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "tianshu.universe.launcher" not in dockerfile
    assert "tianshu.app:create_app" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "COPY build_backend/" in dockerfile
    assert "COPY --from=frontend-builder /src/tianshu/web/static" in dockerfile
    assert "src/tianshu/web/static/" in dockerignore
    assert set(dockerignore.splitlines()) >= _DOCKER_CONTEXT_EXCLUSIONS


def test_gitignore_covers_local_only_release_artifacts_without_hiding_public_source() -> None:
    if not (ROOT / ".git").exists():
        return

    for relative_path in _LOCAL_ONLY_IGNORE_SAMPLES:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", relative_path],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 0, relative_path

    for relative_path in _PUBLIC_SOURCE_SAMPLES:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", relative_path],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 1, relative_path


def test_known_local_only_files_are_not_tracked() -> None:
    if not (ROOT / ".git").exists():
        return

    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    tracked = set(result.stdout.decode().split("\0"))
    assert not (_FORBIDDEN_TRACKED_LOCAL_FILES & tracked)
