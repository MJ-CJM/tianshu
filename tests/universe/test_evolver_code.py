"""Tests for UniverseEvolver.propose_code_variant (2c / Phase 2 increment 2c-C2)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tianshu.universe.evolver import UniverseEvolver
from tianshu.universe.gate import GateResult


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _make_cfg(**overrides):
    defaults = dict(
        code_variant_enabled=True,
        code_variant_eval_set_size=5,
        code_variant_auto_promote=False,
        universe_promote_margin=0.05,
    )
    defaults.update(overrides)
    return type("Cfg", (), defaults)()


def _make_config_manager(cfg=None):
    cm = MagicMock()
    cm.agent_config = cfg or _make_cfg()
    return cm


class FakeManager:
    """Minimal fake for UniverseManager."""

    def __init__(self, champion_uid="champ-001"):
        self._champion_uid = champion_uid
        self.branched: list[dict] = []
        self.archived: list[str] = []

    def champion_id(self) -> str | None:
        return self._champion_uid

    def champion(self) -> dict | None:
        if self._champion_uid:
            return {"id": self._champion_uid, "fitness": {"score": 0.5}}
        return None

    def branch_code_variant(self, parent_id: str, name: str, *, start_ref="HEAD", description="") -> dict:
        uid = f"child-{len(self.branched):03d}"
        rec = {"id": uid, "parent": parent_id, "name": name, "description": description}
        self.branched.append(rec)
        return rec

    def archive(self, uid: str) -> None:
        self.archived.append(uid)


class FakeCodeStore:
    def __init__(self, tmp_path: Path):
        self._root = tmp_path

    def worktree_dir(self, uid: str) -> Path:
        d = self._root / uid
        d.mkdir(parents=True, exist_ok=True)
        return d


class FakeCodeMutator:
    def __init__(self, *, applied: bool = True, reason: str = "ok"):
        self._applied = applied
        self._reason = reason

    async def mutate(self, worktree: Path, *, target_path: str, hypothesis: str) -> dict:
        return {
            "applied": self._applied,
            "target": target_path,
            "commit": "abc123" if self._applied else None,
            "reason": self._reason,
        }


class FakeGate:
    def __init__(self, *, passed: bool = True, stage: str = "ok", detail: str = ""):
        self._result = GateResult(passed=passed, stage=stage, detail=detail)

    def run(self, worktree: Path, *, run_tests: bool = True) -> GateResult:
        return self._result


class FakeEvalHarness:
    def __init__(self, *, fitness_score: float = 0.7, n: int = 3):
        self._score = fitness_score
        self._n = n

    def select_eval_set(self, size: int) -> list[str]:
        return [f"goal-{i}" for i in range(min(size, self._n))]

    def evaluate(self, worktree: Path, *, eval_set: list[str], seed_db=None) -> dict:
        fitness = {"score": self._score, "samples": len(eval_set)}
        stats = {"cost": 0.01, "total": len(eval_set), "success": len(eval_set)}
        return {"fitness": fitness, "stats": stats, "n": len(eval_set)}


class FakeStorage:
    def __init__(self, *, champion_fitness_score: float = 0.5):
        self._champion_score = champion_fitness_score
        self.saved_runs: list[dict] = []
        self.updated_fitness: list[tuple[str, dict]] = []

    def save_variant_eval_run(self, run: dict) -> None:
        self.saved_runs.append(run)

    def update_universe_fitness(self, uid: str, fitness: dict) -> None:
        self.updated_fitness.append((uid, fitness))

    def get_champion_universe(self) -> dict | None:
        return {"id": "champ-001", "fitness": {"score": self._champion_score}}


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def _build_evolver(
    tmp_path: Path,
    *,
    cfg=None,
    manager: FakeManager | None = None,
    storage: FakeStorage | None = None,
    code_mutator: FakeCodeMutator | None = None,
    gate: FakeGate | None = None,
    eval_harness: FakeEvalHarness | None = None,
    omit_collaborators: bool = False,
) -> tuple[UniverseEvolver, FakeManager, FakeStorage]:
    mgr = manager or FakeManager()
    sto = storage or FakeStorage()
    cm = _make_config_manager(cfg)

    code_store = FakeCodeStore(tmp_path)
    mutator = code_mutator or FakeCodeMutator()
    g = gate or FakeGate()
    eh = eval_harness or FakeEvalHarness()

    if omit_collaborators:
        ev = UniverseEvolver(
            llm_client=MagicMock(),
            manager=mgr,
            storage=sto,
            config_manager=cm,
        )
    else:
        ev = UniverseEvolver(
            llm_client=MagicMock(),
            manager=mgr,
            storage=sto,
            config_manager=cm,
            code_store=code_store,
            gate=g,
            eval_harness=eh,
            code_mutator=mutator,
        )
    return ev, mgr, sto


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_disabled_returns_disabled(tmp_path):
    cfg = _make_cfg(code_variant_enabled=False)
    ev, _, _ = _build_evolver(tmp_path, cfg=cfg)
    result = await ev.propose_code_variant(target_path="src/foo.py", hypothesis="try this")
    assert result["status"] == "disabled"


async def test_no_collaborators_when_none_injected(tmp_path):
    ev, _, _ = _build_evolver(tmp_path, omit_collaborators=True)
    result = await ev.propose_code_variant(target_path="src/foo.py", hypothesis="try this")
    assert result["status"] == "no_collaborators"


async def test_no_champion_returns_no_champion(tmp_path):
    mgr = FakeManager(champion_uid=None)
    ev, _, _ = _build_evolver(tmp_path, manager=mgr)
    result = await ev.propose_code_variant(target_path="src/foo.py", hypothesis="try this")
    assert result["status"] == "no_champion"


async def test_no_mutation_archives_and_returns(tmp_path):
    mutator = FakeCodeMutator(applied=False, reason="target outside evolvable allowlist")
    ev, mgr, _ = _build_evolver(tmp_path, code_mutator=mutator)
    result = await ev.propose_code_variant(target_path="src/foo.py", hypothesis="try this")
    assert result["status"] == "no_mutation"
    assert result["detail"] == "target outside evolvable allowlist"
    # archive must be called on the branched child
    assert len(mgr.archived) == 1
    assert mgr.archived[0] == result["universe_id"]


async def test_gate_failed_saves_run_not_archived(tmp_path):
    gate = FakeGate(passed=False, stage="static", detail="SyntaxError in foo.py")
    ev, mgr, sto = _build_evolver(tmp_path, gate=gate)
    result = await ev.propose_code_variant(target_path="src/foo.py", hypothesis="try this")
    assert result["status"] == "gate_failed"
    assert result["detail"] == "static"
    # eval run saved with gate_passed=False
    assert len(sto.saved_runs) == 1
    assert sto.saved_runs[0]["gate_passed"] is False
    assert sto.saved_runs[0]["gate_detail"]["stage"] == "static"
    # NOT archived on gate fail (kept for human inspection)
    assert len(mgr.archived) == 0


async def test_gate_fail_detail_truncated_to_1000(tmp_path):
    long_detail = "x" * 2000
    gate = FakeGate(passed=False, stage="test", detail=long_detail)
    ev, _, sto = _build_evolver(tmp_path, gate=gate)
    await ev.propose_code_variant(target_path="src/foo.py", hypothesis="try this")
    saved_detail = sto.saved_runs[0]["gate_detail"]["detail"]
    assert len(saved_detail) <= 1000


async def test_recommended_when_variant_beats_champion_by_margin(tmp_path):
    # champion score=0.5, variant score=0.7, margin=0.05 → 0.7 >= 0.5+0.05 → recommended
    sto = FakeStorage(champion_fitness_score=0.5)
    eh = FakeEvalHarness(fitness_score=0.7)
    ev, _, sto = _build_evolver(tmp_path, storage=sto, eval_harness=eh)
    result = await ev.propose_code_variant(target_path="src/foo.py", hypothesis="try this")
    assert result["status"] == "recommended"
    assert result["fitness"]["score"] == pytest.approx(0.7)
    # fitness saved to storage
    assert len(sto.updated_fitness) == 1
    assert len(sto.saved_runs) == 1
    assert sto.saved_runs[0]["gate_passed"] is True


async def test_evaluated_when_variant_within_margin(tmp_path):
    # champion score=0.5, variant score=0.52, margin=0.05 → 0.52 < 0.55 → evaluated
    sto = FakeStorage(champion_fitness_score=0.5)
    eh = FakeEvalHarness(fitness_score=0.52)
    ev, _, sto = _build_evolver(tmp_path, storage=sto, eval_harness=eh)
    result = await ev.propose_code_variant(target_path="src/foo.py", hypothesis="try this")
    assert result["status"] == "evaluated"
    assert result["fitness"]["score"] == pytest.approx(0.52)


async def test_explicit_parent_id_used_over_champion(tmp_path):
    mgr = FakeManager(champion_uid="champ-001")
    ev, mgr, _ = _build_evolver(tmp_path, manager=mgr)
    result = await ev.propose_code_variant(
        target_path="src/foo.py", hypothesis="try this", parent_id="explicit-parent",
    )
    # branched from the explicit parent, not champion
    assert mgr.branched[0]["parent"] == "explicit-parent"
    assert result["status"] in {"recommended", "evaluated"}


async def test_error_returns_error_status(tmp_path):
    # Simulate an unexpected exception from manager
    mgr = MagicMock()
    mgr.champion_id.side_effect = RuntimeError("unexpected DB error")
    ev, _, _ = _build_evolver(tmp_path, manager=mgr)
    result = await ev.propose_code_variant(target_path="src/foo.py", hypothesis="try this")
    assert result["status"] == "error"
    assert "unexpected DB error" in result["detail"]


async def test_eval_run_saved_with_required_fields(tmp_path):
    sto = FakeStorage(champion_fitness_score=0.3)
    eh = FakeEvalHarness(fitness_score=0.8, n=5)
    cfg = _make_cfg(code_variant_eval_set_size=5)
    ev, _, sto = _build_evolver(tmp_path, storage=sto, eval_harness=eh, cfg=cfg)
    await ev.propose_code_variant(target_path="src/foo.py", hypothesis="improve throughput")
    assert len(sto.saved_runs) == 1
    run = sto.saved_runs[0]
    assert "id" in run
    assert "universe_id" in run
    assert run["gate_passed"] is True
    assert "fitness" in run
    assert "eval_set_version" in run
    assert "created_at" in run
