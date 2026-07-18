from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
CHECKER_PATH = ROOT / "scripts" / "check_lean_preview_candidate.py"


def _module():
    spec = importlib.util.spec_from_file_location("lean_preview_candidate_checker", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _context(module):
    command_results = {
        command_id: {
            "command": command,
            "exit_code": 0,
            "passed": 1,
            "failed": 0,
            "skipped": 0,
            "deselected": 0,
            "warnings": 0,
            "required_skipped": 0,
            "summary": "passed",
        }
        for command_id, command in module.REQUIRED_FINAL_COMMANDS.items()
    }
    command_results["backend_non_slow"].update(
        {"passed": 4200, "skipped": 2, "deselected": 24, "warnings": 7}
    )
    command_results["packaging"].update({"passed": 28, "warnings": 4})
    command_results["web_unit"].update({"passed": 186, "warnings": 0})
    command_results["web_playwright"].update({"passed": 41, "warnings": 0})
    phase_reports = {
        phase_id: module.PhaseReportInput(
            phase_id=phase_id,
            gate_id=spec.gate_id,
            report_ref=spec.report_ref,
            report_bytes=(
                f"{spec.pass_marker}\nphase: {phase_id}\nexternal_pending: retained, not passed\n"
            ).encode(),
            evidence_commit=spec.evidence_commit,
        )
        for phase_id, spec in module.PHASE_SPECS.items()
    }
    return module.CandidateContext(
        source_commit="1" * 40,
        clean_source=True,
        phase_reports=phase_reports,
        phase_commits_in_history=frozenset(
            spec.evidence_commit for spec in module.PHASE_SPECS.values()
        ),
        demo_report={
            "source_commit": "1" * 40,
            "wheel_sha256": "a" * 64,
            "fixture": False,
            "steps": [{"status": "passed"}] * 13,
            "content_hash": "c" * 64,
        },
        verified_demo=True,
        wheel_sha256="a" * 64,
        observed_wheel_sha256="a" * 64,
        sdist_sha256="b" * 64,
        observed_sdist_sha256="b" * 64,
        capability_matrix=(
            "desktop Web only\n"
            "S4 three pages implemented automation user_approval_pending\n"
            "VoiceOver external_pending\n"
            "Lean Core evolution experimental full G4 external_pending\n"
            "full G5 deferred\n"
            "remote MCP disabled\n"
            "open stdio MCP disabled\n"
            "publication_status: not_authorized\n"
        ),
        deferred_work_ids=module.REQUIRED_DEFERRED_WORK_IDS,
        command_results=command_results,
        screenshot_expected={"control.png": "d" * 64},
        screenshot_observed={"control.png": "d" * 64},
        tracked_idea_paths=(),
        visual_status="user_approval_pending",
        publication_status="not_authorized",
    )


def test_candidate_context_accepts_the_exact_bounded_truth() -> None:
    module = _module()
    module.validate_candidate_context(_context(module))


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("missing_phase", "phase reports"),
        ("wrong_phase_commit", "phase evidence commit"),
        ("wrong_source", "source commit"),
        ("corrupt_wheel", "Wheel hash"),
        ("corrupt_sdist", "sdist hash"),
        ("failed_required", "required Gate failed"),
        ("skipped_required", "required Gate skipped"),
        ("stale_screenshot", "screenshot"),
        ("capability_mismatch", "capability matrix"),
        ("omitted_d8", "deferred work"),
        ("external_counted_pass", "external_pending"),
        ("unverified_demo", "verified non-fixture demo"),
        ("fixture_demo", "verified non-fixture demo"),
        ("fake_visual_approval", "visual status"),
        ("fake_publication", "publication status"),
        ("tracked_idea", "tracked .idea"),
    ],
)
def test_candidate_context_rejects_every_false_candidate_case(case: str, message: str) -> None:
    module = _module()
    context = _context(module)
    if case == "missing_phase":
        context.phase_reports.pop("s5_lean_core")
    elif case == "wrong_phase_commit":
        context.phase_commits_in_history = frozenset()
    elif case == "wrong_source":
        context.demo_report["source_commit"] = "2" * 40
    elif case == "corrupt_wheel":
        context.observed_wheel_sha256 = "e" * 64
    elif case == "corrupt_sdist":
        context.observed_sdist_sha256 = "e" * 64
    elif case == "failed_required":
        context.command_results["mypy"]["failed"] = 1
    elif case == "skipped_required":
        context.command_results["packaging"]["required_skipped"] = 1
    elif case == "stale_screenshot":
        context.screenshot_observed["control.png"] = "e" * 64
    elif case == "capability_mismatch":
        context.capability_matrix = context.capability_matrix.replace("full G5 deferred", "")
    elif case == "omitted_d8":
        context.deferred_work_ids = context.deferred_work_ids[:-1]
    elif case == "external_counted_pass":
        phase = context.phase_reports["s5_lean_core"]
        context.phase_reports["s5_lean_core"] = module.PhaseReportInput(
            phase_id=phase.phase_id,
            gate_id=phase.gate_id,
            report_ref=phase.report_ref,
            report_bytes=phase.report_bytes + b"external_pending: passed\n",
            evidence_commit=phase.evidence_commit,
        )
    elif case == "unverified_demo":
        context.verified_demo = False
    elif case == "fixture_demo":
        context.demo_report["fixture"] = True
    elif case == "fake_visual_approval":
        context.visual_status = "user_approved"
    elif case == "fake_publication":
        context.publication_status = "authorized"
    else:
        context.tracked_idea_paths = (".idea/workspace.xml",)

    with pytest.raises(module.CandidateGateError, match=message):
        module.validate_candidate_context(context)


def test_exact_packaging_gates_do_not_launch_uv() -> None:
    for relative in (
        "tests/resources/test_wheel_manifest.py",
        "tests/packaging/test_fresh_wheel_demo.py",
        "tests/launch/test_lean_preview_fresh_wheel.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert '["uv",' not in source
        assert "['uv'," not in source


def test_only_the_bound_generated_demo_batch_may_follow_a_clean_source_build() -> None:
    module = _module()
    demo_ref = "docs/cc-fable-v1/evidence/lean-preview/batch-1/demo-report.json"

    assert module.only_bound_demo_evidence_is_dirty(
        (
            demo_ref,
            "docs/cc-fable-v1/evidence/lean-preview/batch-1/artifacts/01-ready.json",
        ),
        demo_ref,
    )
    assert not module.only_bound_demo_evidence_is_dirty(("src/tianshu/app.py",), demo_ref)
    assert not module.only_bound_demo_evidence_is_dirty(
        ("docs/cc-fable-v1/evidence/lean-preview/other/demo-report.json",),
        demo_ref,
    )
