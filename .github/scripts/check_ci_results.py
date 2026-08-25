"""Fail unless the exact required CI jobs all completed successfully."""

from __future__ import annotations

import json
import os
import sys

_RESULTS_ENV = "REQUIRED_JOB_RESULTS"
_EXPECTED_JOBS = (
    "dependency-review",
    "backend",
    "frontend",
    "web-e2e",
    "release-wheel",
)
_KNOWN_RESULTS = frozenset({"success", "failure", "cancelled", "skipped"})


def _load_results() -> dict[str, str]:
    raw = os.environ.get(_RESULTS_ENV)
    if raw is None:
        raise ValueError(f"{_RESULTS_ENV} is not set")

    payload: object = json.loads(raw)
    if not isinstance(payload, dict) or not all(isinstance(job, str) for job in payload):
        raise ValueError("results must be a JSON object keyed by job id")

    actual_jobs = set(payload)
    expected_jobs = set(_EXPECTED_JOBS)
    missing = sorted(expected_jobs - actual_jobs)
    unexpected = sorted(actual_jobs - expected_jobs)
    if missing:
        raise ValueError(f"missing jobs: {', '.join(missing)}")
    if unexpected:
        raise ValueError(f"unexpected jobs: {', '.join(unexpected)}")

    results: dict[str, str] = {}
    for job in _EXPECTED_JOBS:
        job_data = payload[job]
        if not isinstance(job_data, dict):
            raise ValueError(f"{job} result entry must be an object")
        result = job_data.get("result")
        if not isinstance(result, str) or result not in _KNOWN_RESULTS:
            raise ValueError(f"{job} has invalid result: {result!r}")
        results[job] = result
    return results


def main() -> int:
    try:
        results = _load_results()
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"required CI results invalid: {exc}", file=sys.stderr)
        return 1

    failed = [f"{job}={result}" for job, result in results.items() if result != "success"]
    if failed:
        print(
            f"required CI jobs did not succeed: {', '.join(failed)}",
            file=sys.stderr,
        )
        return 1

    print("required CI jobs: success")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
