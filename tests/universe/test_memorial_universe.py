"""Test Memorial.universe_id roundtrip through Storage."""

from tianshu.models import Edict, Memorial


def test_memorial_universe_id_roundtrip(storage):
    """Save a Memorial with universe_id and verify it survives the roundtrip."""
    edict = Edict(goal="test universe attribution")
    storage.save_edict(edict)

    memorial = Memorial(edict_id=edict.id, instruction="do it", universe_id="u1")
    storage.save_memorial(memorial)

    loaded = storage.get_memorial(memorial.id)
    assert loaded is not None
    assert loaded.universe_id == "u1"


def test_memorial_universe_id_defaults_to_none(storage):
    """Memorial.universe_id is None when not set (backward compat)."""
    edict = Edict(goal="test")
    storage.save_edict(edict)

    memorial = Memorial(edict_id=edict.id)
    storage.save_memorial(memorial)

    loaded = storage.get_memorial(memorial.id)
    assert loaded.universe_id is None


def test_memorial_universe_id_survives_update(storage):
    """universe_id is preserved after update_memorial."""
    edict = Edict(goal="test")
    storage.save_edict(edict)

    memorial = Memorial(edict_id=edict.id, universe_id="u42")
    storage.save_memorial(memorial)

    memorial.result = "done"
    storage.update_memorial(memorial)

    loaded = storage.get_memorial(memorial.id)
    assert loaded.universe_id == "u42"
