"""Legacy Universe projection must be backed by a pre-existing real assignment."""

from datetime import UTC, datetime

import pytest

from tianshu.models.evolution_candidate import CandidateVersionRefV1
from tianshu.models.run_assignment import RunAssignmentV1


def _assignment(*, challenger: bool = False) -> RunAssignmentV1:
    champion = CandidateVersionRefV1(
        version="champion-v1",
        artifact_digest="a" * 64,
        canonical_digest="a" * 64,
    )
    selected = CandidateVersionRefV1(
        version="challenger-v1",
        artifact_digest="b" * 64,
        canonical_digest="b" * 64,
    )
    return RunAssignmentV1(
        assignment_id="assignment-1",
        memorial_id="mem-1",
        candidate_id="candidate-1",
        champion_ref=champion,
        selected_ref=selected if challenger else champion,
        routing_version=1,
        bucket=0,
        created_at=datetime(2026, 7, 18, tzinfo=UTC),
    )


class _FakeRouter:
    def __init__(self, assignment=None):
        self.assignment = assignment
        self.reads = []

    def get(self, memorial_id):
        self.reads.append(memorial_id)
        return self.assignment


class _FakeStorage:
    def __init__(self, champion=None, universes=()):
        self._champ = champion
        self._unis = list(universes)

    def get_champion_universe(self):
        return self._champ

    def list_universes(self, include_archived=True):
        return [u for u in self._unis if include_archived or u["status"] != "archived"]


def _mgr(champion, universes=(), *, router=None):
    from tianshu.universe.manager import UniverseManager

    return UniverseManager(
        storage=_FakeStorage(champion, universes),
        store=None,
        persona_loader=None,
        skills_loader=None,
        config_snapshot=lambda: {},
        config_apply=lambda m: None,
        challenger_router=router,
    )


def test_route_returns_legacy_champion_projection_only_after_assignment_read():
    champ = {"id": "u-champ", "status": "champion"}
    challenger = {"id": "u-chal", "status": "challenger", "code_ref": None}
    router = _FakeRouter(_assignment())
    mgr = _mgr(champ, [champ, challenger], router=router)

    assert mgr.route_for_memorial("mem-1") == "u-champ"
    assert router.reads == ["mem-1"]


def test_route_returns_none_without_legacy_champion_after_assignment_read():
    assert _mgr(None, router=_FakeRouter(_assignment())).route_for_memorial("mem-1") is None


def test_challenger_assignment_never_falsely_returns_the_champion_universe():
    champion = {"id": "u-champ", "status": "champion"}

    assert (
        _mgr(champion, router=_FakeRouter(_assignment(challenger=True))).route_for_memorial("mem-1")
        is None
    )


def test_route_never_falls_back_or_lazy_assigns_without_authoritative_state():
    with pytest.raises(RuntimeError, match="challenger_router_required"):
        _mgr({"id": "u-champ"}).route_for_memorial("mem-1")

    router = _FakeRouter(assignment=None)
    with pytest.raises(LookupError, match="run assignment not found"):
        _mgr({"id": "u-champ"}, router=router).route_for_memorial("mem-1")
    assert not hasattr(router, "assign")
