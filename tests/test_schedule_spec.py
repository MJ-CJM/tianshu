"""Tests for scheduler.schedule_spec.parse_spec."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tianshu.scheduler.schedule_spec import parse_spec


def test_every_interval():
    s = parse_spec("every 2h")
    assert s.type == "interval"
    assert s.interval_seconds == 7200

    assert parse_spec("every 30m").interval_seconds == 1800
    assert parse_spec("every 45s").interval_seconds == 45
    assert parse_spec("every 1d").interval_seconds == 86400


def test_relative_once():
    before = datetime.now(UTC)
    s = parse_spec("30m")
    assert s.type == "once"
    assert s.at is not None
    delta = (s.at - before).total_seconds()
    assert 1700 < delta <= 1800 + 5  # ~30 min ahead


def test_cron():
    s = parse_spec("0 9 * * *")
    assert s.type == "cron"
    assert s.cron == "0 9 * * *"


def test_iso_with_tz():
    s = parse_spec("2026-06-10T09:00:00+08:00")
    assert s.type == "once"
    assert s.at is not None
    assert s.at.tzinfo is not None
    assert s.at.utcoffset().total_seconds() == 8 * 3600


def test_iso_naive_uses_timezone():
    s = parse_spec("2026-06-10T09:00:00", timezone="Asia/Shanghai")
    assert s.type == "once"
    assert s.at.utcoffset().total_seconds() == 8 * 3600


def test_invalid_raises():
    with pytest.raises(ValueError):
        parse_spec("not-a-schedule")
    with pytest.raises(ValueError):
        parse_spec("")
