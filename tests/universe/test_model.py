"""Tests for Universe model — code_ref roundtrip."""

from tianshu.universe.model import Universe, UniverseOrigin


def test_code_ref_roundtrip():
    u = Universe(name="cv", origin=UniverseOrigin.CODE_VARIANT, code_ref="universe/abc")
    row = u.to_row()
    assert row["code_ref"] == "universe/abc"
    back = Universe.from_row(row)
    assert back.code_ref == "universe/abc"
    assert back.origin == UniverseOrigin.CODE_VARIANT


def test_code_ref_defaults_none():
    u = Universe(name="data")
    assert u.code_ref is None
    assert u.to_row()["code_ref"] is None
    assert Universe.from_row(u.to_row()).code_ref is None
