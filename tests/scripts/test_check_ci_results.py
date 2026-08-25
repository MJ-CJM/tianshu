from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_SCRIPT = _ROOT / ".github" / "scripts" / "check_ci_results.py"
_EXPECTED_JOBS = (
    "dependency-review",
    "backend",
    "frontend",
    "web-e2e",
    "release-wheel",
)


def _success_results() -> dict[str, dict[str, object]]:
    return {job: {"result": "success", "outputs": {}} for job in _EXPECTED_JOBS}


def _run_checker(
    results: Mapping[str, object] | str | None,
) -> subprocess.CompletedProcess[str]:
    assert _SCRIPT.is_file(), f"missing CI result checker: {_SCRIPT}"
    env = os.environ.copy()
    if results is None:
        env.pop("REQUIRED_JOB_RESULTS", None)
    elif isinstance(results, str):
        env["REQUIRED_JOB_RESULTS"] = results
    else:
        env["REQUIRED_JOB_RESULTS"] = json.dumps(results)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_all_required_ci_jobs_succeed() -> None:
    result = _run_checker(_success_results())

    assert result.returncode == 0, result.stderr
    assert "required CI jobs: success" in result.stdout


@pytest.mark.parametrize("job", _EXPECTED_JOBS)
@pytest.mark.parametrize("status", ("failure", "skipped", "cancelled"))
def test_any_non_success_required_job_fails(job: str, status: str) -> None:
    results = _success_results()
    results[job]["result"] = status

    result = _run_checker(results)

    assert result.returncode == 1
    assert f"{job}={status}" in result.stderr


def test_missing_required_job_fails() -> None:
    results = _success_results()
    results.pop("frontend")

    result = _run_checker(results)

    assert result.returncode == 1
    assert "missing jobs: frontend" in result.stderr


def test_unexpected_job_fails() -> None:
    results = _success_results()
    results["unreviewed-job"] = {"result": "success", "outputs": {}}

    result = _run_checker(results)

    assert result.returncode == 1
    assert "unexpected jobs: unreviewed-job" in result.stderr


@pytest.mark.parametrize(
    "results",
    (
        "not json",
        "[]",
        json.dumps({"backend": None}),
    ),
)
def test_malformed_results_fail_closed(results: str) -> None:
    result = _run_checker(results)

    assert result.returncode == 1
    assert "required CI results invalid" in result.stderr


def test_missing_results_environment_fails_closed() -> None:
    result = _run_checker(None)

    assert result.returncode == 1
    assert "REQUIRED_JOB_RESULTS is not set" in result.stderr


def test_unknown_job_result_fails_closed() -> None:
    results = _success_results()
    results["backend"]["result"] = "neutral"

    result = _run_checker(results)

    assert result.returncode == 1
    assert "backend has invalid result" in result.stderr
