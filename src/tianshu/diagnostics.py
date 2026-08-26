"""结构化诊断：版本化 DoctorReport 与 liveness/readiness 评估（G1.5）。

契约（design/15 D/E 段 + design/18 B2/B3）：
- Doctor 默认只读：不 mkdir、不写文件、不跑迁移、不构造 LLM 客户端；
  既有 DB 以 SQLite URI ``mode=ro`` + ``PRAGMA query_only=ON`` 打开。
- 证据 allow-list：仅 bool/int/短枚举字符串/短哈希；绝不输出 key 前缀、
  secret 路径、header、query、原始异常文本、绝对路径、用户名。
- readiness 为纯评估：required 失败 → ``not_ready``（HTTP 层 503）；
  可选集成只降级为 ``degraded``。
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"

CheckStatus = Literal["pass", "degraded", "fail", "skipped"]

# 证据值 allow-list：布尔/整数/短字符串（枚举、版本号、12 位短哈希等）
_MAX_EVIDENCE_STR = 64


def _safe_evidence(values: dict[str, object]) -> dict[str, bool | int | str]:
    """证据 allow-list 护栏：越界类型/超长字符串直接拒绝（防未来检查项泄漏）。"""
    out: dict[str, bool | int | str] = {}
    for key, value in values.items():
        if isinstance(value, bool | int):
            out[key] = value
        elif isinstance(value, str):
            if len(value) > _MAX_EVIDENCE_STR or "/" in value or "\\" in value:
                raise ValueError(f"evidence value for {key!r} violates allow-list")
            out[key] = value
        else:
            raise ValueError(f"evidence type for {key!r} violates allow-list")
    return out


@dataclass(frozen=True)
class DoctorCheck:
    id: str
    status: CheckStatus
    required: bool
    evidence: dict[str, bool | int | str] = field(default_factory=dict)
    remediation: str = ""


@dataclass(frozen=True)
class DoctorReport:
    schema_version: str
    status: Literal["pass", "degraded", "fail"]
    checks: tuple[DoctorCheck, ...]

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "checks": [
                {
                    "id": c.id,
                    "status": c.status,
                    "required": c.required,
                    "evidence": c.evidence,
                    "remediation": c.remediation,
                }
                for c in self.checks
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def to_table_text(self) -> str:
        lines = [f"doctor report v{self.schema_version} status={self.status}"]
        for c in self.checks:
            req = "required" if c.required else "optional"
            ev = " ".join(f"{k}={v}" for k, v in sorted(c.evidence.items()))
            line = f"  [{c.status:>8}] {c.id} ({req})"
            if ev:
                line += f" {ev}"
            if c.remediation and c.status in ("fail", "degraded"):
                line += f" -> {c.remediation}"
            lines.append(line)
        return "\n".join(lines)


def _overall(checks: tuple[DoctorCheck, ...]) -> Literal["pass", "degraded", "fail"]:
    if any(c.status == "fail" and c.required for c in checks):
        return "fail"
    if any(c.status in ("fail", "degraded") for c in checks):
        return "degraded"
    return "pass"


# --- Doctor checks（纯只读，settings → DoctorCheck） ---


def _check_runtime_profile(settings) -> DoctorCheck:
    profile = settings.startup_profile
    ok = profile in ("live", "demo")
    return DoctorCheck(
        id="runtime.profile",
        status="pass" if ok else "fail",
        required=True,
        evidence=_safe_evidence({"profile": profile}),
        remediation="" if ok else "TIANSHU_STARTUP_PROFILE 只接受 live 或 demo",
    )


def _check_auth_mode(settings) -> DoctorCheck:
    return DoctorCheck(
        id="auth.mode",
        status="pass",
        required=False,
        evidence=_safe_evidence({"security_mode": settings.security_mode}),
    )


def provider_config_check(
    *, profile: str, effective: object | None, config_source: str
) -> DoctorCheck:
    """provider/profile 的**唯一**判定源：Doctor 与 readiness 共用，禁止各自实现。

    判定对象是**运行时实际会用到的 active LLM 配置**（``ConfigManager.state``），
    不是 env/settings —— env 只是空库首启时的种子。读 env 会同时产生两种谎言：

    - 假就绪：env 有 key，但 active 配置被清空/切到空配置 → 每条 Edict 首次 LLM
      调用即失败，而健康端点报 ready，编排器照样放流量进来。
    - 假不就绪：key 只存在于 ``llm_configs``（经 Web UI 配置、env 未设）→ 实例
      完全可用却被永久判 not_ready，k8s/LB 会把它摘除。
    """
    if profile == "demo":
        return DoctorCheck(
            id="provider.config",
            status="pass",
            required=True,
            evidence=_safe_evidence({"profile": "demo", "network_required": False}),
        )
    if effective is None:
        return DoctorCheck(
            id="provider.config",
            status="degraded",
            required=True,
            evidence=_safe_evidence({"config_source": config_source, "resolved": False}),
            remediation="无法读取运行时生效的 LLM 配置；确认数据库可读后重试",
        )
    has_key = bool(getattr(effective, "api_key", ""))
    has_model = bool(getattr(effective, "model", ""))
    ok = has_key and has_model
    return DoctorCheck(
        id="provider.config",
        status="pass" if ok else "fail",
        required=True,
        evidence=_safe_evidence(
            {
                "api_key_present": has_key,
                "model_configured": has_model,
                "config_source": config_source,
            }
        ),
        remediation=""
        if ok
        else "当前生效的 LLM 配置缺 api_key/model；在 Web 配置页补齐或设置 "
        "TIANSHU_LLM_API_KEY 与 TIANSHU_LLM_MODEL，或使用 demo 档位",
    )


def _check_provider_config(settings) -> DoctorCheck:
    effective, source = _resolve_effective_llm_config(settings)
    return provider_config_check(
        profile=settings.startup_profile, effective=effective, config_source=source
    )


def _resolve_effective_llm_config(settings) -> tuple[object | None, str]:
    """只读解析"运行时实际会用到的 active LLM 配置"（Doctor 侧）。

    与 ``ConfigManager`` 同构：``llm_configs`` 有行 → ``is_active`` 那一行；
    无行 → env 种子（ConfigManager 首启时正是拿它建配置）。不能直接构造
    ConfigManager —— 它在空库时会 ``_persist()`` 写库，违反 Doctor 只读契约。

    返回 ``(config, source)``，source ∈ ``db_active`` / ``env_seed`` / ``unknown``。

    已知边界（诚实标注）：legacy ``PUT /config`` 只改进程内存、不落库，故
    进程内的 active 配置可能与库里的不一致。Doctor 是进程外工具，只能看见
    持久化的事实；运行中服务的真实就绪由 ``server.ready``（探活跑中的
    ``/health/ready``）反映。
    """
    from tianshu.config_manager import LLMConfigState

    env_seed = LLMConfigState(
        name=settings.llm_model,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        api_base=settings.llm_api_base,
    )
    db_path = Path(settings.db_path).expanduser()
    if not db_path.is_file():
        return env_seed, "env_seed"
    try:
        conn, _mode = _open_db_readonly(db_path)
    except (sqlite3.Error, DoctorDatabaseUnavailable):
        return None, "unknown"
    try:
        # 排序与选取规则必须与 ConfigManager._load_from_db 逐字同构：它经
        # list_llm_configs()（ORDER BY is_active DESC, name ASC）遍历，_active_name
        # 被**最后**一个 is_active 行覆盖。多 active 行时若这里取第一行，Doctor 会
        # 与运行时选中不同的配置——同一事实两个结论。
        rows = conn.execute(
            "SELECT name, model, provider_id, api_base, is_active FROM llm_configs "
            "ORDER BY is_active DESC, name ASC"
        ).fetchall()
        active = next((row for row in reversed(rows) if row[4]), None) if rows else None
        provider_row = None
        credential_present = False
        if active is not None and str(active[2] or ""):
            provider_row = conn.execute(
                "SELECT profile_id, api_key_ref FROM model_providers WHERE id = ?",
                (str(active[2]),),
            ).fetchone()
            if provider_row is not None and str(provider_row[1] or "") == "credential":
                credential_present = (
                    conn.execute(
                        "SELECT 1 FROM network_credentials WHERE kind = 'llm_provider' "
                        "AND provider_name = ? AND deleted_at IS NULL AND enabled = 1",
                        (str(active[2]),),
                    ).fetchone()
                    is not None
                )
    except sqlite3.Error:
        return None, "unknown"
    finally:
        conn.close()
    if not rows:
        return env_seed, "env_seed"
    if active is None:
        return None, "unknown"
    return (
        LLMConfigState(
            name=str(active[0]),
            model=str(active[1]),
            api_key=_infer_provider_key(provider_row, credential_present),
            api_base=str(active[3] or ""),
            provider_id=str(active[2] or ""),
        ),
        "db_active",
    )


def _infer_provider_key(provider_row: object | None, credential_present: bool) -> str:
    """只读推断 active 配置的 key 可用性（与 registry.resolve_key 同构，不解密）。

    key 已迁入 network_credentials(kind='llm_provider')/env 引用（迁移 0020，
    llm_configs 明文列已删）。Doctor 不持有 vault，只判"运行时能否解出 key"：
    加密凭证在库且主密钥在场 → 可用（占位串 '<vault>'，仅供 bool 判定与证据，
    永不外泄真实 key）；$ENV / profile env 引用直接读环境变量。
    """
    from tianshu.providers.profiles import get_profile

    if provider_row is None:
        return ""
    ref = str(provider_row[1] or "")
    if ref == "credential":
        if credential_present and os.getenv("TIANSHU_SECRET_MASTER_KEY"):
            return "<vault>"
        return ""
    if ref.startswith("$ENV:"):
        return os.getenv(ref[len("$ENV:") :], "")
    profile = get_profile(str(provider_row[0] or ""))
    if profile is not None and profile.key_env:
        return os.getenv(profile.key_env, "")
    return ""


class DoctorDatabaseUnavailable(Exception):
    """只读诊断无法在"不落盘"的前提下读取该数据库。"""


def _open_db_readonly(db_path: Path) -> tuple[sqlite3.Connection, str]:
    """只读打开，**绝不新建 ``-shm``**（正常形态下不新建任何文件）。返回 ``(连接, 快照模式)``。

    WAL 库的普通 ``mode=ro`` 连接需要 ``-shm`` 共享索引；``-shm`` 不存在时
    SQLite 会创建它 —— 那是写文件，违反 Doctor 只读契约（且只读目录下直接
    抛错，把健康库误判为 fail）。判定键因此是 ``-shm`` 而不是"任一 sidecar"。

    诚实边界：``-shm`` 在而 ``-wal`` 缺失是 SQLite 自身不会产生的异常形态（两者
    成对创建/删除；崩溃则两者都在），只有外部干预（手工清理、备份时排除 ``-wal``）
    才可达。此时共享模式仍可能物化一个空 ``-wal``。不为此把健康的运行中库判成
    degraded —— 那会造出比这个边角更常见的假警报。

    - ``-shm`` 已存在（服务运行中）：``mode=ro``。不新建文件，且能正确读到 WAL
      内容。注：SQLite 读者会在共享内存索引里登记 read-mark —— 不新建文件、
      不改数据库内容，是 WAL 读取的正常机制。
    - ``-shm`` 不存在：
      * 无 ``-wal``（或 ``-wal`` 为空）→ ``mode=ro&immutable=1``：不建文件、不取锁。
        WAL 已回放完毕，忽略它不会漏读任何已提交数据。
      * ``-wal`` 非空（备份/崩溃残留）→ 读到那些已提交帧就必须物化 ``-shm``，
        而 ``immutable=1`` 会**静默丢弃**它们。此时拒绝打开，由调用方转
        ``degraded``：宁可说"读不到"，也不给出比事实更强的结论。

    残余 TOCTOU：``immutable`` 分支在 stat 与真正读之间若有写者启动并提交，
    快照会陈旧。连接后复检 ``-shm`` 已能覆盖绝大多数情形（写者一定会物化它）；
    无法根除的部分由证据里的 ``snapshot_mode=immutable`` 如实标注。
    """
    shm = db_path.with_name(db_path.name + "-shm")
    wal = db_path.with_name(db_path.name + "-wal")

    def _wal_has_frames() -> bool:
        try:
            return wal.is_file() and wal.stat().st_size > 0
        except OSError:
            return True  # 读不到就当它有内容：宁可 degraded，也不静默丢数据

    if not shm.exists():
        if _wal_has_frames():
            raise DoctorDatabaseUnavailable("wal_pending")
        conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
        if not shm.exists():  # 复检：期间没有写者物化 -shm，快照有效
            conn.execute("PRAGMA query_only=ON")
            return conn, "immutable"
        conn.close()  # 写者已到场 → 改用 shared（-shm 已在，仍不新建文件）
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn, "shared"


def _check_database_connectivity(settings) -> DoctorCheck:
    db_path = Path(settings.db_path).expanduser()
    if not db_path.is_file():
        return DoctorCheck(
            id="database.connectivity",
            status="degraded",
            required=True,
            evidence=_safe_evidence({"exists": False, "created": False}),
            remediation="数据库将在首次启动时创建；Doctor 不代为创建",
        )
    try:
        conn, mode = _open_db_readonly(db_path)
        try:
            rows = conn.execute("PRAGMA quick_check").fetchall()
            ok = bool(rows) and all(str(r[0]).lower() == "ok" for r in rows)
        finally:
            conn.close()
    except DoctorDatabaseUnavailable:
        # 有未回放的 WAL 但无 -shm：只读读不到它们，且物化 -shm 会破坏只读契约
        return DoctorCheck(
            id="database.connectivity",
            status="degraded",
            required=True,
            evidence=_safe_evidence({"exists": True, "readable": False, "wal_pending": True}),
            remediation="存在未回放的 WAL 且无共享索引；只读诊断不代为物化，"
            "请在服务运行时改看 /health/ready，或先干净停机让 WAL 回放",
        )
    except sqlite3.Error:
        # 打不开 ≠ 库有病（例如只读目录）；不把健康库误判为 fail
        return DoctorCheck(
            id="database.connectivity",
            status="degraded",
            required=True,
            evidence=_safe_evidence({"exists": True, "readable": False}),
            remediation="数据库无法只读打开；检查目录/文件权限，服务运行中可稍后重试",
        )
    return DoctorCheck(
        id="database.connectivity",
        status="pass" if ok else "fail",
        required=True,
        evidence=_safe_evidence(
            {"exists": True, "readable": True, "quick_check_ok": ok, "snapshot_mode": mode}
        ),
        remediation="" if ok else "PRAGMA quick_check 未通过；从备份恢复或重建数据库",
    )


def _check_database_migrations(settings) -> DoctorCheck:
    db_path = Path(settings.db_path).expanduser()
    if not db_path.is_file():
        return DoctorCheck(
            id="database.migrations",
            status="skipped",
            required=True,
            evidence=_safe_evidence({"exists": False}),
        )
    from tianshu.storage.migration_ledger import MigrationError, pending_migrations
    from tianshu.storage.migrations import MIGRATIONS

    try:
        conn, _mode = _open_db_readonly(db_path)
        try:
            pending = pending_migrations(conn, MIGRATIONS)
        finally:
            conn.close()
    except MigrationError:
        return DoctorCheck(
            id="database.migrations",
            status="fail",
            required=True,
            evidence=_safe_evidence({"ledger_readable": False, "drift_detected": True}),
            remediation="迁移台账漂移或不可读；勿手工修改 schema_migrations",
        )
    except (sqlite3.Error, DoctorDatabaseUnavailable):
        # 与 connectivity 同口径：读不到 ≠ 迁移漂移
        return DoctorCheck(
            id="database.migrations",
            status="degraded",
            required=True,
            evidence=_safe_evidence({"ledger_readable": False, "drift_detected": False}),
            remediation="数据库无法只读检查迁移状态；检查目录/文件权限或未回放的 WAL",
        )
    count = len(pending)
    return DoctorCheck(
        id="database.migrations",
        status="pass" if count == 0 else "fail",
        required=True,
        evidence=_safe_evidence({"pending_count": count, "latest_version": MIGRATIONS[-1].version}),
        remediation="" if count == 0 else "启动服务以应用待执行迁移（Doctor 不代为执行）",
    )


def _check_resources_package() -> DoctorCheck:
    from tianshu.resources import catalog

    try:
        departments = sorted(
            node.name for node in catalog.persona_defaults().iterdir() if node.is_dir()
        )
        skills = sorted(node.name for node in catalog.builtin_skills().iterdir() if node.is_dir())
        digest = catalog.package_digest(("personas",))["personas"][:12]
    except Exception:  # noqa: BLE001 - 资源缺损统一转稳定失败码
        return DoctorCheck(
            id="resources.package",
            status="fail",
            required=True,
            evidence=_safe_evidence({"locatable": False}),
            remediation="打包资源缺损；重装 wheel 或检查安装完整性",
        )
    ok = len(departments) >= 7 and len(skills) >= 2  # 六部门 + court；两个内建技能
    return DoctorCheck(
        id="resources.package",
        status="pass" if ok else "fail",
        required=True,
        evidence=_safe_evidence(
            {
                "locatable": True,
                "persona_dirs": len(departments),
                "builtin_skills": len(skills),
                "personas_digest12": digest,
            }
        ),
        remediation="" if ok else "打包 persona/skill 资源不完整；重装 wheel",
    )


def _check_overlay_writable(settings) -> DoctorCheck:
    import os

    results: dict[str, object] = {}
    ok = True
    for label, raw in (
        ("personas_overlay", settings.runtime_personas_dir),
        ("memory_overlay", settings.memory_dir),
    ):
        path = Path(raw).expanduser()
        # 只读检查：不创建目录；目录存在测自身，否则测最近存在的祖先
        probe = path
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        writable = os.access(probe, os.W_OK)
        results[f"{label}_exists"] = path.exists()
        results[f"{label}_writable"] = writable
        ok = ok and writable
    return DoctorCheck(
        id="overlay.writable",
        status="pass" if ok else "fail",
        required=True,
        evidence=_safe_evidence(results),
        remediation="" if ok else "overlay 目录不可写；检查 HOME 权限或相关 TIANSHU_*_DIR 配置",
    )


def _check_workspace_git(settings) -> DoctorCheck:
    ws = Path(settings.workspace_dir).expanduser()
    exists = ws.is_dir()
    is_git = (ws / ".git").exists() if exists else False
    return DoctorCheck(
        id="workspace.git",
        status="pass" if exists else "degraded",
        required=False,
        evidence=_safe_evidence({"exists": exists, "git_repository": is_git}),
        remediation="" if exists else "TIANSHU_WORKSPACE_DIR 指向的目录不存在",
    )


def _inferred_repo_root() -> Path:
    import tianshu as _pkg

    return Path(_pkg.__file__).resolve().parents[2]


def _check_repo_root(settings, *, check_id: str, configured: str, env_name: str) -> DoctorCheck:
    """env_name 必须是 TianshuSettings 真实字段派生的变量名。

    不可从 check_id 反推：``evals.repo_root`` 对应的字段是 ``eval_repo_root``
    （env ``TIANSHU_EVAL_REPO_ROOT``），派生会得到不存在的 ``TIANSHU_EVALS_*``，
    而 settings 的 ``extra="ignore"`` 会把错名静默吞掉。
    """
    if configured:
        return DoctorCheck(
            id=check_id,
            status="pass",
            required=False,
            evidence=_safe_evidence({"explicit": True}),
        )
    inferred_is_git = (_inferred_repo_root() / ".git").exists()
    return DoctorCheck(
        id=check_id,
        status="pass" if inferred_is_git else "degraded",
        required=False,
        evidence=_safe_evidence({"explicit": False, "inferred_is_git": inferred_is_git}),
        remediation="" if inferred_is_git else f"wheel 部署下请显式设置 {env_name}",
    )


def _check_server(settings, *, probe_server: bool) -> list[DoctorCheck]:
    if not probe_server:
        return [
            DoctorCheck(id="server.port", status="skipped", required=False),
            DoctorCheck(id="server.live", status="skipped", required=False),
            DoctorCheck(id="server.ready", status="skipped", required=False),
        ]
    port = settings.port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        occupied = s.connect_ex(("127.0.0.1", port)) == 0
    checks = [
        DoctorCheck(
            id="server.port",
            status="pass",
            required=False,
            evidence=_safe_evidence({"port": port, "in_use": occupied}),
        )
    ]
    if not occupied:
        checks.append(DoctorCheck(id="server.live", status="skipped", required=False))
        checks.append(DoctorCheck(id="server.ready", status="skipped", required=False))
        return checks
    import httpx

    for check_id, path in (("server.live", "/health/live"), ("server.ready", "/health/ready")):
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}{path}", timeout=2.0)
            body_status = str(resp.json().get("status", ""))[:16]
            status: CheckStatus = "pass" if resp.status_code == 200 else "fail"
            checks.append(
                DoctorCheck(
                    id=check_id,
                    status=status,
                    required=False,
                    evidence=_safe_evidence(
                        {"http_status": resp.status_code, "reported": body_status}
                    ),
                    remediation="" if status == "pass" else "服务未就绪；查看服务日志",
                )
            )
        except Exception:  # noqa: BLE001 - 端口被占但非天枢/未答复，转稳定证据
            checks.append(
                DoctorCheck(
                    id=check_id,
                    status="degraded",
                    required=False,
                    evidence=_safe_evidence({"responded": False}),
                    remediation="端口被占用但健康端点未应答；确认占用进程",
                )
            )
    return checks


def _check_sandbox_capability() -> DoctorCheck:
    from tianshu.executor import capabilities as caps

    try:
        probe = caps.probe_host_capabilities()
        # 真实字段：sandbox_backend（None = 未探测到容器运行时）
        container = probe.sandbox_backend is not None
    except Exception:  # noqa: BLE001
        return DoctorCheck(
            id="sandbox.capability",
            status="degraded",
            required=False,
            evidence=_safe_evidence({"probe_ok": False}),
        )
    return DoctorCheck(
        id="sandbox.capability",
        status="pass",
        required=False,
        # 检测到容器可执行文件只是线索，不等于强沙箱证明（G1.6 负责真实容器证据）
        evidence=_safe_evidence({"probe_ok": True, "container_hint": container}),
    )


def _check_default_search_provider() -> DoctorCheck:
    """默认档位的默认搜索引擎，在这份发行物里到底能不能用。

    ``NETWORK_DEFAULT`` / ``NETWORK_RESEARCH`` 的 ``search_provider`` 默认是
    duckduckgo，而它硬依赖 lxml（web extra）。核心发行物里该引擎不会注册，
    web_search 会返回 ``provider_not_registered:duckduckgo``——这是个明确错误，
    但运维在跑之前看不见。这里让它在 doctor 里可见。
    """
    from tianshu.tools.hongluisi.policy import NETWORK_DEFAULT

    provider = NETWORK_DEFAULT.search_provider
    if provider is None:
        return DoctorCheck(
            id="network.search_provider",
            status="skipped",
            required=False,
            evidence=_safe_evidence({"configured": False}),
        )
    try:
        from tianshu.tools.hongluisi.engine_registry import get_registered_search_providers

        registered = provider in get_registered_search_providers()
    except Exception:  # noqa: BLE001
        registered = False
    return DoctorCheck(
        id="network.search_provider",
        status="pass" if registered else "degraded",
        required=False,
        evidence=_safe_evidence({"provider": provider, "registered": registered}),
        remediation=""
        if registered
        else f"默认搜索引擎 {provider} 未注册（缺可选依赖）；装 tianshu-agent-os[web] 或改用其他 provider",
    )


def _check_optional_dependency(check_id: str, module: str) -> DoctorCheck:
    import importlib.util

    present = importlib.util.find_spec(module) is not None
    return DoctorCheck(
        id=check_id,
        status="pass" if present else "skipped",
        required=False,
        evidence=_safe_evidence({"installed": present}),
    )


def _check_build_identity() -> DoctorCheck:
    from tianshu.resources import catalog

    ver = catalog.version()
    return DoctorCheck(
        id="build.identity",
        status="pass",
        required=False,
        evidence=_safe_evidence({"version": ver[:32]}),
    )


def run_doctor_checks(settings, *, probe_server: bool = True) -> DoctorReport:
    """只读诊断入口。固定检查顺序，输出确定性报告。"""
    checks: list[DoctorCheck] = [
        _check_runtime_profile(settings),
        _check_auth_mode(settings),
        _check_provider_config(settings),
        _check_database_connectivity(settings),
        _check_database_migrations(settings),
        _check_resources_package(),
        _check_overlay_writable(settings),
        _check_workspace_git(settings),
        *_check_server(settings, probe_server=probe_server),
        _check_sandbox_capability(),
        _check_default_search_provider(),
        _check_optional_dependency("mcp.integration", "mcp"),
        _check_optional_dependency("optional.feishu", "lark_oapi"),
        _check_optional_dependency("optional.telegram", "telegram"),
        _check_repo_root(
            settings,
            check_id="universe.repo_root",
            configured=settings.universe_repo_root,
            env_name="TIANSHU_UNIVERSE_REPO_ROOT",
        ),
        _check_repo_root(
            settings,
            check_id="evals.repo_root",
            configured=settings.eval_repo_root,
            env_name="TIANSHU_EVAL_REPO_ROOT",
        ),
        _check_build_identity(),
    ]
    frozen = tuple(checks)
    return DoctorReport(schema_version=SCHEMA_VERSION, status=_overall(frozen), checks=frozen)


# --- Readiness 评估（纯函数；输入由 app 层组装为回调/布尔） ---


@dataclass(frozen=True)
class ReadinessInputs:
    database_ok: Callable[[], bool]
    migrations_current: Callable[[], bool]
    scheduler_ready: Callable[[], bool]
    worker_ready: Callable[[], bool]
    outbox_ready: Callable[[], bool]
    dispatcher_ready: Callable[[], bool]
    decision_ready: Callable[[], bool]
    attempt_ready: Callable[[], bool]
    artifact_ready: Callable[[], bool]
    delivery_ready: Callable[[], bool]
    resources_ok: Callable[[], bool]
    # provider：真实可用性判定（复用 provider_config_check，不是 profile 字符串回显）
    provider_ready: Callable[[], bool]
    provider_profile: Callable[[], str | None]  # 仅用于证据展示
    workspace_ready: Callable[[], bool]
    # Serving generations are traffic-critical; cleanup-only drift remains degraded below.
    generation_runtime_ready: Callable[[], bool]
    # rollback_pending is operationally degraded: traffic is sealed, restore is incomplete.
    evolution_rollback_ready: Callable[[], bool]
    # Operator kill switch is distinct from a rollback that has not converged.
    evolution_routing_enabled: Callable[[], bool]
    # Cleanup-only generation drift is degraded, not a pending candidate rollback.
    evolution_generation_cleanup_pending: Callable[[], bool]
    # 可选集成：name -> None(未启用) | True(健康) | False(启用但异常)
    optional_integrations: Callable[[], dict[str, bool | None]]


@dataclass(frozen=True)
class ReadinessReport:
    schema_version: str
    status: Literal["ready", "degraded", "not_ready"]
    checks: tuple[DoctorCheck, ...]

    def to_summary_dict(self) -> dict:
        """secure-remote 匿名响应：恰好 schema_version + status，无内部细节。"""
        return {"schema_version": self.schema_version, "status": self.status}

    def to_detail_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "checks": [
                {
                    "id": c.id,
                    "status": c.status,
                    "required": c.required,
                    "evidence": c.evidence,
                }
                for c in self.checks
            ],
        }


def _guarded(fn: Callable[[], object]) -> tuple[object | None, bool]:
    """返回 ``(值, 是否抛错)``。

    探针抛错必须**留痕**：只回 None 会让"探针自己坏了"与"依赖坏了"混为一谈，
    更糟的是让整个检查从报告里蒸发（MCP 假属性 bug 正是这么隐身的）。
    """
    try:
        return fn(), False
    except Exception:  # noqa: BLE001 - readiness 评估自身不允许抛出
        logger.exception("readiness probe raised; recording as failure evidence")
        return None, True


def assess_readiness(inputs: ReadinessInputs) -> ReadinessReport:
    required_specs: tuple[tuple[str, Callable[[], bool]], ...] = (
        ("database", inputs.database_ok),
        ("migrations", inputs.migrations_current),
        ("scheduler", inputs.scheduler_ready),
        ("worker", inputs.worker_ready),
        ("outbox", inputs.outbox_ready),
        ("dispatcher", inputs.dispatcher_ready),
        ("decision", inputs.decision_ready),
        ("attempt", inputs.attempt_ready),
        ("artifact", inputs.artifact_ready),
        ("delivery", inputs.delivery_ready),
        ("resources", inputs.resources_ok),
        ("workspace", inputs.workspace_ready),
        ("generation.runtime", inputs.generation_runtime_ready),
    )
    checks: list[DoctorCheck] = []
    for check_id, fn in required_specs:
        value, errored = _guarded(fn)
        ok = value is True
        evidence: dict[str, object] = {"ok": ok}
        if errored:
            evidence["probe_error"] = True
        checks.append(
            DoctorCheck(
                id=check_id,
                status="pass" if ok else "fail",
                required=True,
                evidence=_safe_evidence(evidence),
            )
        )
    provider_value, provider_errored = _guarded(inputs.provider_ready)
    provider_ok = provider_value is True
    profile, _ = _guarded(inputs.provider_profile)
    profile_label = (
        profile if isinstance(profile, str) and profile in ("live", "demo") else "unknown"
    )
    provider_evidence: dict[str, object] = {"ok": provider_ok, "profile": profile_label}
    if provider_errored:
        provider_evidence["probe_error"] = True
    checks.append(
        DoctorCheck(
            id="provider",
            status="pass" if provider_ok else "fail",
            required=True,
            evidence=_safe_evidence(provider_evidence),
            remediation=""
            if provider_ok
            else "当前生效的 LLM 配置缺 api_key/model；在 Web 配置页补齐或改用 demo 档位",
        )
    )
    routing_value, routing_errored = _guarded(inputs.evolution_routing_enabled)
    routing_enabled = routing_value is True
    rollback_value, rollback_errored = _guarded(inputs.evolution_rollback_ready)
    rollback_ready = rollback_value is True
    cleanup_value, cleanup_errored = _guarded(inputs.evolution_generation_cleanup_pending)
    generation_cleanup_pending = cleanup_value is True
    rollback_ok = routing_enabled and rollback_ready and not generation_cleanup_pending
    rollback_evidence: dict[str, object] = {"ok": rollback_ok}
    if routing_errored or rollback_errored or cleanup_errored:
        rollback_evidence["probe_error"] = True
    elif not routing_enabled:
        rollback_evidence["routing_disabled"] = True
    elif not rollback_ready:
        rollback_evidence["rollback_pending"] = True
    elif generation_cleanup_pending:
        rollback_evidence["generation_cleanup_pending"] = True
    if rollback_ok:
        rollback_remediation = ""
    elif not routing_enabled and not routing_errored:
        rollback_remediation = "Evolution routing 已由全局开关关闭；确认排空与回退完成后再重新开放"
    elif generation_cleanup_pending and not cleanup_errored:
        rollback_remediation = "Runtime generation 正在排空清理；确认旧代释放完成"
    else:
        rollback_remediation = "Evolution rollback 尚未验证完成；保持流量封闭并检查恢复日志"
    checks.append(
        DoctorCheck(
            id="evolution.rollback",
            status="pass" if rollback_ok else "degraded",
            required=False,
            evidence=_safe_evidence(rollback_evidence),
            remediation=rollback_remediation,
        )
    )
    optional, optional_errored = _guarded(inputs.optional_integrations)
    if optional_errored or not isinstance(optional, dict):
        # 探针异常绝不让检查消失：如实记一条 degraded，运维才看得见"探针坏了"
        checks.append(
            DoctorCheck(
                id="optional.probe",
                status="degraded",
                required=False,
                evidence=_safe_evidence({"probe_error": True}),
                remediation="可选集成探针异常；查看服务日志",
            )
        )
    else:
        for name, state in sorted(optional.items()):
            if state is None:
                status: CheckStatus = "skipped"
            elif state:
                status = "pass"
            else:
                status = "degraded"
            checks.append(
                DoctorCheck(
                    id=f"optional.{name}",
                    status=status,
                    required=False,
                    evidence=_safe_evidence({"enabled": state is not None, "ok": bool(state)}),
                )
            )
    frozen = tuple(checks)
    if any(c.required and c.status != "pass" for c in frozen):
        status_overall: Literal["ready", "degraded", "not_ready"] = "not_ready"
    elif any(c.status == "degraded" for c in frozen):
        status_overall = "degraded"
    else:
        status_overall = "ready"
    return ReadinessReport(schema_version=SCHEMA_VERSION, status=status_overall, checks=frozen)
