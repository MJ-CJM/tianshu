"""把人类友好的调度表达式解析为 EdictSchedule。

支持四种写法（按此优先级匹配）：
- 周期间隔： ``every 2h`` / ``every 30m``           → interval
- 相对一次： ``30m`` / ``2h`` / ``1d`` / ``45s``     → once（at = now + Δ）
- cron 表达式： ``0 9 * * *``                        → cron
- ISO 8601 时间点： ``2026-06-10T09:00:00+08:00``    → once（at = 该时刻）

参考 hermes ``cronjob`` 工具的 schedule 参数：让对话中可以用自然的时间描述下发定时/周期任务。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from tianshu.models.edict import EdictSchedule

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_REL_RE = re.compile(r"^(\d+)\s*([smhd])$", re.IGNORECASE)
_EVERY_RE = re.compile(r"^every\s+(\d+)\s*([smhd])$", re.IGNORECASE)


def parse_spec(spec: str, timezone: str = "UTC") -> EdictSchedule:
    """Parse a schedule spec string into an EdictSchedule. Raises ValueError if invalid."""
    if not spec or not spec.strip():
        raise ValueError("schedule 不能为空")
    s = spec.strip()
    tz = timezone or "UTC"

    m = _EVERY_RE.match(s)
    if m:
        secs = int(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]
        if secs < 1:
            raise ValueError("interval 间隔必须 ≥ 1 秒")
        return EdictSchedule(type="interval", interval_seconds=secs, timezone=tz)

    m = _REL_RE.match(s)
    if m:
        secs = int(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]
        at = datetime.now(UTC) + timedelta(seconds=secs)
        return EdictSchedule(type="once", at=at, timezone=tz)

    if croniter.is_valid(s):
        return EdictSchedule(type="cron", cron=s, timezone=tz)

    # ISO 8601 时间点
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        raise ValueError(
            f"无法解析 schedule={spec!r}：支持 '30m'/'2h'、'every 2h'、"
            "cron('0 9 * * *')、或 ISO 8601 时间（'2026-06-10T09:00:00+08:00'）",
        ) from None
    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(tz))
        except ZoneInfoNotFoundError:
            dt = dt.replace(tzinfo=UTC)
    return EdictSchedule(type="once", at=dt, timezone=tz)


__all__ = ["parse_spec"]
