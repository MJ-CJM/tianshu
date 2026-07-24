"""per-run scoped token —— 客卿 LLM 出口的一次性凭证(OpenShell 凭证隔离核心)。

天枢为每次客卿 spawn 铸一个 scoped token(前缀 tskq_),绑定 {edict_id, run_id,
模型白名单, 预算, TTL}。客卿进程 env 里只有这个 token + 网关 baseUrl,**raw provider key
永不进客卿进程**——真实 key 只在天枢网关侧,由网关按 token 校验后上游注入。

token 明文只在铸造时返回一次;存储只留 sha256 哈希(照搬 gateway/auth.py 的 _hash_token
范式)。预算硬熔断:record_spend 累加实际花费,超额即 over-budget → 网关返回 402
(pi 归类为额度耗尽、不重试)。token 可按 run_id 即时吊销。

P3 版为进程内内存存储(per-run token 短时 TTL、ephemeral);持久化/跨进程共享待网关
多实例时再上 SQLite。
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass

_TOKEN_PREFIX = "tskq_"
_USD_TO_CNY = 7.2  # 与 cost/tracker.py、keqing executor 同款单一汇率口径


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


@dataclass
class ScopedTokenRecord:
    """一个 per-run scoped token 的服务端记录(只存哈希,不存明文)。"""

    token_hash: str
    edict_id: str
    run_id: str
    model_allowlist: frozenset[str]
    budget_cny: float | None  # None = 不限额(仅测试/特批)
    expires_at: float  # epoch seconds
    spent_cny: float = 0.0
    revoked_at: float | None = None

    def is_active(self, *, now: float) -> bool:
        return self.revoked_at is None and now < self.expires_at

    def allows_model(self, model: str) -> bool:
        # 空白名单 = 不限模型(仅在未配置策略时);非空则精确匹配。
        return not self.model_allowlist or model in self.model_allowlist

    def is_over_budget(self) -> bool:
        return self.budget_cny is not None and self.spent_cny >= self.budget_cny


class ScopedTokenStore:
    """线程安全的 scoped token 台账。铸造/校验/记账/吊销。"""

    def __init__(self) -> None:
        self._by_hash: dict[str, ScopedTokenRecord] = {}
        self._by_run: dict[str, str] = {}  # run_id → token_hash(即时吊销用)
        self._lock = threading.Lock()

    def mint(
        self,
        *,
        edict_id: str,
        run_id: str,
        model_allowlist: frozenset[str] | set[str] | None,
        budget_cny: float | None,
        ttl_seconds: float,
        now: float | None = None,
    ) -> str:
        """铸一个新 token,返回**明文**(仅此一次)。同一 run 重复铸造会顶掉旧的。"""
        now = time.time() if now is None else now
        raw = _TOKEN_PREFIX + secrets.token_urlsafe(32)
        record = ScopedTokenRecord(
            token_hash=_hash_token(raw),
            edict_id=edict_id,
            run_id=run_id,
            model_allowlist=frozenset(model_allowlist or ()),
            budget_cny=budget_cny,
            expires_at=now + ttl_seconds,
        )
        with self._lock:
            prev = self._by_run.get(run_id)
            if prev is not None:
                self._by_hash.pop(prev, None)  # 同 run 旧 token 作废
            self._by_hash[record.token_hash] = record
            self._by_run[run_id] = record.token_hash
        return raw

    def verify(self, raw_token: str, *, now: float | None = None) -> ScopedTokenRecord | None:
        """校验 token:未知/过期/已吊销 → None;否则返回记录(不含明文)。"""
        now = time.time() if now is None else now
        with self._lock:
            record = self._by_hash.get(_hash_token(raw_token))
            if record is None or not record.is_active(now=now):
                return None
            return record

    def record_spend(self, raw_token: str, cost_usd: float) -> ScopedTokenRecord | None:
        """把一次真实花费(美元)累加到 token(×7.2 转 CNY)。返回更新后的记录。

        网关每次上游请求后调用;返回记录的 is_over_budget() 为 True 时网关应对
        **后续**请求返回 402(额度耗尽)。
        """
        with self._lock:
            record = self._by_hash.get(_hash_token(raw_token))
            if record is None:
                return None
            record.spent_cny += max(0.0, cost_usd) * _USD_TO_CNY
            return record

    def revoke_run(self, run_id: str, *, now: float | None = None) -> bool:
        """按 run_id 即时吊销(estop/run 结束)。返回是否吊销了一个活 token。"""
        now = time.time() if now is None else now
        with self._lock:
            token_hash = self._by_run.get(run_id)
            if token_hash is None:
                return False
            record = self._by_hash.get(token_hash)
            if record is None or record.revoked_at is not None:
                return False
            record.revoked_at = now
            return True

    def gc_expired(self, *, now: float | None = None) -> int:
        """回收过期/已吊销 token,返回回收数。"""
        now = time.time() if now is None else now
        with self._lock:
            dead = [
                h
                for h, r in self._by_hash.items()
                if r.revoked_at is not None or now >= r.expires_at
            ]
            for h in dead:
                self._by_hash.pop(h, None)
            self._by_run = {rid: h for rid, h in self._by_run.items() if h not in dead}
            return len(dead)


# 当前 spawn 的 scoped token 明文(会话执行器 spawn 前 set,secret_resolver 读取注入客卿 env)。
# contextvar 随同一 task 的 await 传播:执行器 execute() → gateway.start() → _build_environment
# → resolver 全在同一 async 上下文,故 resolver 能读到执行器设的值。明文永不落台账(只存哈希)。
_current_run_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "keqing_run_gateway_token", default=None
)


def set_current_run_token(token: str | None) -> object:
    """设当前 run 的网关 token 明文;返回 reset 句柄供 spawn 后清理。"""
    return _current_run_token.set(token)


def reset_current_run_token(handle: object) -> None:
    with contextlib.suppress(Exception):
        _current_run_token.reset(handle)  # type: ignore[arg-type]


def get_current_run_token() -> str | None:
    return _current_run_token.get()


_DEFAULT_STORE: ScopedTokenStore | None = None
_DEFAULT_LOCK = threading.Lock()


def get_scoped_token_store() -> ScopedTokenStore:
    """进程级单例(网关与 secret_resolver 共用同一台账)。"""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_STORE is None:
                _DEFAULT_STORE = ScopedTokenStore()
    return _DEFAULT_STORE
