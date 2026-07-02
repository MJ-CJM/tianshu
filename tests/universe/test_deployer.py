"""Unit tests for Deployer — injected fake relaunch, no real os.execv."""

from __future__ import annotations

from tianshu.universe.deployer import Deployer, DeployPointer, DeployRecord

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class FakeRelaunch:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1


def make_deployer(tmp_path, fake_relaunch, health_timeout_s=30):
    pointer = DeployPointer(tmp_path / "deploy.json")
    return Deployer(pointer, relaunch=fake_relaunch, health_timeout_s=health_timeout_s), pointer


# ---------------------------------------------------------------------------
# DeployPointer tests
# ---------------------------------------------------------------------------


def test_pointer_empty_when_missing(tmp_path):
    pointer = DeployPointer(tmp_path / "deploy.json")
    current, previous = pointer.read()
    assert current is None
    assert previous is None


def test_pointer_roundtrip(tmp_path):
    pointer = DeployPointer(tmp_path / "deploy.json")
    rec_a = DeployRecord(ref="abc123", worktree="/wt/a")
    rec_b = DeployRecord(ref="def456", worktree=None)
    pointer.write(rec_a, rec_b)
    current, previous = pointer.read()
    assert current == rec_a
    assert previous == rec_b


# ---------------------------------------------------------------------------
# Deployer.promote
# ---------------------------------------------------------------------------


def test_promote_writes_pointer_and_relaunches(tmp_path):
    fake = FakeRelaunch()
    deployer, pointer = make_deployer(tmp_path, fake)

    deployer.promote(ref="abc", worktree="/wt/abc")

    current, _ = pointer.read()
    assert current is not None
    assert current.ref == "abc"
    assert current.worktree == "/wt/abc"
    assert fake.calls == 1


def test_promote_pushes_old_current_to_previous(tmp_path):
    fake = FakeRelaunch()
    deployer, pointer = make_deployer(tmp_path, fake)

    # Seed an existing current = A
    rec_a = DeployRecord(ref="aaa", worktree="/wt/a")
    pointer.write(rec_a, None)

    deployer.promote(ref="bbb", worktree="/wt/b")

    current, previous = pointer.read()
    assert current is not None
    assert current.ref == "bbb"
    assert previous is not None
    assert previous.ref == "aaa"


# ---------------------------------------------------------------------------
# Deployer.stage
# ---------------------------------------------------------------------------


def test_stage_writes_pointer_no_relaunch(tmp_path):
    fake = FakeRelaunch()
    deployer, pointer = make_deployer(tmp_path, fake)

    # Seed an existing current = A
    rec_a = DeployRecord(ref="aaa", worktree="/wt/a")
    pointer.write(rec_a, None)

    deployer.stage(ref="bbb", worktree="/wt/b")

    # Pointer written: new current=bbb, previous=aaa
    current, previous = pointer.read()
    assert current is not None
    assert current.ref == "bbb"
    assert current.worktree == "/wt/b"
    assert previous is not None
    assert previous.ref == "aaa"
    # relaunch NOT called
    assert fake.calls == 0


# ---------------------------------------------------------------------------
# Deployer.rollback
# ---------------------------------------------------------------------------


def test_rollback_restores_previous(tmp_path):
    fake = FakeRelaunch()
    deployer, pointer = make_deployer(tmp_path, fake)

    rec_a = DeployRecord(ref="aaa", worktree="/wt/a")
    rec_b = DeployRecord(ref="bbb", worktree="/wt/b")
    pointer.write(rec_b, rec_a)  # current=B, previous=A

    deployer.rollback()

    current, previous = pointer.read()
    assert current is not None
    assert current.ref == "aaa"
    assert previous is None
    assert fake.calls == 1


def test_rollback_noop_without_previous(tmp_path):
    fake = FakeRelaunch()
    deployer, pointer = make_deployer(tmp_path, fake)

    rec_b = DeployRecord(ref="bbb", worktree="/wt/b")
    pointer.write(rec_b, None)  # current=B, previous=None

    deployer.rollback()

    # Pointer unchanged, relaunch NOT called
    current, previous = pointer.read()
    assert current is not None
    assert current.ref == "bbb"
    assert previous is None
    assert fake.calls == 0


# ---------------------------------------------------------------------------
# Deployer.verify_or_rollback
# ---------------------------------------------------------------------------


def test_verify_or_rollback_healthy(tmp_path, monkeypatch):
    fake = FakeRelaunch()
    deployer, pointer = make_deployer(tmp_path, fake)

    rec_b = DeployRecord(ref="bbb", worktree="/wt/b")
    rec_a = DeployRecord(ref="aaa", worktree="/wt/a")
    pointer.write(rec_b, rec_a)

    monkeypatch.setattr(deployer, "await_health", lambda url: True)

    result = deployer.verify_or_rollback("http://localhost:8080")

    assert result is True
    assert fake.calls == 0  # no rollback relaunch
    current, _ = pointer.read()
    assert current is not None
    assert current.ref == "bbb"  # unchanged


def test_verify_or_rollback_unhealthy_triggers_rollback(tmp_path, monkeypatch):
    fake = FakeRelaunch()
    deployer, pointer = make_deployer(tmp_path, fake)

    rec_a = DeployRecord(ref="aaa", worktree="/wt/a")
    rec_b = DeployRecord(ref="bbb", worktree="/wt/b")
    pointer.write(rec_b, rec_a)  # current=B, previous=A

    monkeypatch.setattr(deployer, "await_health", lambda url: False)

    result = deployer.verify_or_rollback("http://localhost:8080")

    assert result is False
    # Pointer rolled back to A
    current, previous = pointer.read()
    assert current is not None
    assert current.ref == "aaa"
    assert previous is None
    # relaunch called once (by rollback)
    assert fake.calls == 1
