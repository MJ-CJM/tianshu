"""FetchRouter：按 profile + override 决定 engine 链与 fallback。

Spec Section 5.4。
"""

from __future__ import annotations

import logging

from tianshu.tools.hongluisi.engines import FetchAttempt, FetchEngine, FetchOutcome
from tianshu.tools.hongluisi.policy import NetworkPolicy

logger = logging.getLogger(__name__)


class FetchRouter:
    def __init__(
        self,
        engines: dict[str, FetchEngine],
        policy: NetworkPolicy,
        override: str | None,
    ) -> None:
        self._engines = engines
        if override is not None:
            # 手动钉死 + 强制关闭 fallback
            self._chain: tuple[str, ...] = (override,)
            self._fallback_mode = "none"
        else:
            self._chain = policy.fetch_engines
            self._fallback_mode = policy.fallback_mode
        self._max_depth = policy.max_fallback_depth

    async def dispatch(self, url: str) -> tuple[FetchOutcome, list[FetchAttempt]]:
        attempts: list[FetchAttempt] = []
        outcome: FetchOutcome | None = None
        for engine_name in self._chain[: self._max_depth]:
            engine = self._engines.get(engine_name)
            if engine is None:
                attempts.append(FetchAttempt(engine_name, "skipped", "not registered"))
                continue
            try:
                outcome = await engine.fetch(url)
            except Exception as e:
                logger.exception("engine %s raised", engine_name)
                outcome = FetchOutcome(
                    content="",
                    status="error",
                    http_status=None,
                    reason=f"engine_exception:{type(e).__name__}",
                    bytes_fetched=0,
                    final_url=None,
                )
            attempts.append(FetchAttempt(engine_name, outcome.status, outcome.reason))
            if outcome.status == "ok":
                return outcome, attempts
            if self._fallback_mode == "none":
                return outcome, attempts
            # on_error_or_empty：empty 和 error 都继续
        if outcome is None:
            outcome = FetchOutcome(
                content="",
                status="error",
                http_status=None,
                reason="no_engine_available",
                bytes_fetched=0,
                final_url=None,
            )
        return outcome, attempts
