from pathlib import Path

from tianshu.storage.migrations import MIGRATIONS


def test_s1_s2_handoff_has_green_gate_and_live_tail() -> None:
    report = Path("docs/cc-fable-v1/reports/g1.5-report.md").read_text()
    assert "status: passed" in report
    assert MIGRATIONS[-1].name == "0006_seed_default_personas"
    versions = [migration.version for migration in MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1))
