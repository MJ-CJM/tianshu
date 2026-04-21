"""per-(edict_id, tool_name) 令牌桶。Spec Section 5.6。

简单实现：deque 记录过去 60s 内的调用时间戳；
超过配额时返回 (False, retry_after_sec)。
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateCheckResult:
    allowed: bool
    retry_after_sec: float = 0.0


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(
        self, edict_id: str, tool_name: str, rate_per_min: int
    ) -> RateCheckResult:
        key = (edict_id, tool_name)
        now = time.monotonic()
        window_start = now - 60.0
        async with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] < window_start:
                bucket.popleft()
            if len(bucket) >= rate_per_min:
                oldest = bucket[0]
                retry = max(0.0, (oldest + 60.0) - now)
                return RateCheckResult(allowed=False, retry_after_sec=retry)
            bucket.append(now)
            return RateCheckResult(allowed=True)


# 进程单例
_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter
