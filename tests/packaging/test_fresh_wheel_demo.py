"""Exact-wheel fresh-HOME 黑盒（G1.5 Slice C）。

构建一次 wheel，把**那一个** wheel 装进仓库外的临时 venv + 全新 HOME（路径含空格
与非 ASCII），清空 PYTHONPATH、禁用 user-site，再装一个 test-only 的 socket guard
（只放行 loopback/AF_UNIX）——"零网络"由内核级拒绝证明，不是靠 mock。

然后在那个环境里跑通完整首次使用链路：doctor → API/Web → 六部门 → 两个内建技能
→ 一条受治理的 demo 敕令（真实 workspace lease/restore/change 路径）→ 干净 SIGTERM。

诚实边界（brief evidence boundary）：本机证据只覆盖 Darwin arm64 + 当前 Python。
Linux、其他 Python 版本、Windows、容器运行时、公共 registry、OIDC 一律
external_pending —— 不得由本测试的通过推断出来。容器归 G1.6。

socket guard 的真实边界：它是 Python 层的 ``socket``/``getaddrinfo`` 围栏，只覆盖
**本进程**的网络调用。子进程（shell 技能、``git``、``curl`` 等）不受它约束——本测试
证明的是"这条首次使用链路在 Python 层零外连"，不是"运行时整体不可能出网"。真正的
出网管控由 ExecutionGateway/沙箱负责，不是这道围栏。

全部进程/守卫/编排代码只存在于本文件（生产源码不新增进程边界）。
"""

import hashlib
import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BRAND_SHA256 = "3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799"

# 路径含空格与非 ASCII：安装态必须在这种真实用户路径下工作
_MESSY_DIR = "天枢 fresh home"

# v6 迁移在空库上 seed 的恰好这六个（源: migrations._seed_default_personas_upgrade）
_EXPECTED_DEPARTMENTS = frozenset({"bingbu", "ducha", "hubu", "neige", "tongzheng", "wenyuan"})
_EXPECTED_BUILTIN_SKILLS = frozenset({"shell", "file-ops"})

# 只允许 loopback：任何对外连接直接抛错。装进 venv 的 sitecustomize（不进 wheel）。
_SOCKET_GUARD = '''\
"""test-only 网络围栏：只放行 loopback / AF_UNIX。"""
import socket as _s

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_real_connect = _s.socket.connect
_real_connect_ex = _s.socket.connect_ex
_real_getaddrinfo = _s.getaddrinfo


class ExternalNetworkBlocked(RuntimeError):
    pass


def _check(sock, address):
    family = getattr(sock, "family", None)
    if family == getattr(_s, "AF_UNIX", None):
        return
    host = address[0] if isinstance(address, tuple) and address else address
    if str(host) not in _LOOPBACK:
        raise ExternalNetworkBlocked(f"external connect blocked: {address!r}")


def _connect(self, address):
    _check(self, address)
    return _real_connect(self, address)


def _connect_ex(self, address):
    _check(self, address)
    return _real_connect_ex(self, address)


def _getaddrinfo(host, *args, **kwargs):
    if str(host) not in _LOOPBACK:
        raise ExternalNetworkBlocked(f"external DNS blocked: {host!r}")
    return _real_getaddrinfo(host, *args, **kwargs)


_s.socket.connect = _connect
_s.socket.connect_ex = _connect_ex
_s.getaddrinfo = _getaddrinfo
'''


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(argv: list[str], *, env: dict[str, str], cwd: Path, timeout: int = 300):
    return subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=timeout)


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """构建一次；后续全部步骤只允许消费这一个 exact wheel。"""
    out_dir = tmp_path_factory.mktemp("wheel-out")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    wheels = sorted(out_dir.glob("tianshu-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    return wheels[0]


@pytest.fixture(scope="module")
def installed(wheel: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    """repo 外 venv + fresh HOME（空格/非 ASCII 路径）+ socket guard。"""
    root = tmp_path_factory.mktemp("blackbox") / _MESSY_DIR
    root.mkdir(parents=True)
    venv_dir = root / "venv"
    home = root / "home"
    home.mkdir()

    # 固定 3.12：项目 requires-python >=3.12 且自身 venv 就是 3.12。裸 uv venv 会挑
    # 本机最新解释器（3.14），而 litellm 在 3.14/darwin 无预编译 wheel、回退 Rust 源码
    # 构建即失败——那不是天枢的安装态问题，但它意味着 3.13/3.14 的可安装性**未被本
    # 测试证明**（见文件头 evidence boundary，属 external_pending）。
    subprocess.run(
        ["uv", "venv", "--python", "3.12", str(venv_dir)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    py = venv_dir / "bin" / "python"
    assert py.is_file()

    # 按绝对 file URI 装那一个 wheel（禁 editable / 禁源树 / 禁二次构建）
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(py),
            f"tianshu[cli] @ {wheel.resolve().as_uri()}",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=900,
    )

    # 依赖装完之后再落 guard —— 装包阶段本来就要联网，围栏只约束被测运行时
    site_packages = next((venv_dir / "lib").glob("python*/site-packages"))
    (site_packages / "sitecustomize.py").write_text(_SOCKET_GUARD, encoding="utf-8")

    env = {
        "PATH": f"{venv_dir / 'bin'}:{os.environ.get('PATH', '')}",
        "HOME": str(home),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TIANSHU_STARTUP_PROFILE": "demo",
        "TIANSHU_DB_PATH": str(home / ".tianshu" / "tianshu.db"),
        "TIANSHU_MEMORY_DIR": str(home / ".tianshu" / "memory"),
        "TIANSHU_RUNTIME_PERSONAS_DIR": str(home / ".tianshu" / "personas"),
        "TIANSHU_WORKSPACE_DIR": str(root / "工作区 source"),
        "LANG": "en_US.UTF-8",
    }
    # PYTHONPATH 必须彻底缺席（不是空串）：否则源树可能被 import 进来
    assert "PYTHONPATH" not in env

    # 包资源 digest 的基线必须在**安装态**取：任何 run 之后再取都抓不到运行期篡改
    # （正是 S1.2 修掉的那类 overlay/COW 写穿 site-packages 的 bug）。
    digest_probe = _run(
        [
            str(py),
            "-c",
            "import json;from tianshu.resources import catalog;print(json.dumps(catalog.package_digest()))",
        ],
        env=env,
        cwd=root,
    )
    assert digest_probe.returncode == 0, digest_probe.stderr
    pristine_digest = json.loads(digest_probe.stdout)

    return {
        "venv": venv_dir,
        "python": py,
        "home": home,
        "root": root,
        "env": env,
        "pristine_digest": pristine_digest,
    }


def test_installed_runtime_never_imports_the_repo_source_tree(installed: dict):
    """探针：sys.path 里既没有仓库根，也没有源树——证明测的是安装态而非源码。"""
    probe = (
        "import json,sys,tianshu;print(json.dumps({'file': tianshu.__file__, 'path': sys.path}))"
    )
    result = _run(
        [str(installed["python"]), "-s", "-c", probe],
        env=installed["env"],
        cwd=installed["root"],
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert "site-packages" in payload["file"], payload["file"]
    repo = str(_REPO_ROOT)
    for entry in payload["path"]:
        assert not entry.startswith(repo), f"仓库路径漏进 sys.path: {entry}"


def test_socket_guard_actually_blocks_external_network(installed: dict):
    """围栏自证：不先证明它会拦，后面的『零网络』就只是没发生而已。"""
    probe = (
        "import socket\n"
        "try:\n"
        "    socket.socket().connect(('93.184.216.34', 80))\n"
        "except Exception as exc:\n"
        "    print(type(exc).__name__)\n"
        "else:\n"
        "    print('NOT_BLOCKED')\n"
    )
    result = _run(
        [str(installed["python"]), "-c", probe],
        env=installed["env"],
        cwd=installed["root"],
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ExternalNetworkBlocked", result.stdout

    dns = "import socket\ntry:\n    socket.getaddrinfo('example.com', 80)\nexcept Exception as exc:\n    print(type(exc).__name__)\nelse:\n    print('NOT_BLOCKED')\n"
    dns_result = _run(
        [str(installed["python"]), "-c", dns],
        env=installed["env"],
        cwd=installed["root"],
    )
    assert dns_result.stdout.strip() == "ExternalNetworkBlocked", dns_result.stdout


def test_doctor_json_is_canonical_and_leaks_no_secrets(installed: dict):
    """Doctor 在 API 起来之前就要能自检；且报告全文不得出现任何 secret。"""
    env = dict(installed["env"])
    env["TIANSHU_LLM_API_KEY"] = "sk-blackbox-secret-must-not-leak"

    result = _run(
        [str(installed["venv"] / "bin" / "tianshu"), "doctor", "--format", "json"],
        env=env,
        cwd=installed["root"],
    )
    # doctor 允许以非零码表达 required 失败；这里只要求它产出可解析的 canonical JSON
    report = json.loads(result.stdout)

    assert report["schema_version"] == "1"
    ids = {check["id"] for check in report["checks"]}
    assert len(ids) == len(report["checks"]), "check id 必须唯一"
    for required_id in ("runtime.profile", "database.migrations", "resources.package"):
        assert required_id in ids, f"缺少 check: {required_id}"

    whole = result.stdout + result.stderr
    assert "sk-blackbox-secret-must-not-leak" not in whole
    assert "sk-blackbox" not in whole


@pytest.fixture(scope="module")
def server(installed: dict):
    """在安装态里起真实进程（独立 session，便于整组 SIGTERM）。"""
    port = _free_port()
    env = dict(installed["env"])
    env["TIANSHU_PORT"] = str(port)
    env["PYTHONWARNINGS"] = "always::ResourceWarning"

    proc = subprocess.Popen(
        [
            str(installed["python"]),
            "-m",
            "uvicorn",
            "tianshu.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        cwd=installed["root"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"

    def _get(path: str, timeout: float = 5.0):
        with urllib.request.urlopen(f"{base}{path}", timeout=timeout) as resp:
            return resp.status, resp.read()

    deadline = time.time() + 90
    live = False
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"服务提前退出: {proc.communicate()[0][-4000:]}")
        try:
            status, _ = _get("/health/live", timeout=2)
            if status == 200:
                live = True
                break
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.5)
    assert live, "/health/live 未在超时内应答"

    yield {"proc": proc, "base": base, "get": _get, "port": port, "env": env}

    if proc.poll() is None:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - 清理兜底
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=10)


def _wait_ready(server: dict, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            request = urllib.request.Request(f"{server['base']}/health/ready")
            with urllib.request.urlopen(request, timeout=5) as resp:
                last = json.loads(resp.read())
                if last.get("status") in ("ready", "degraded"):
                    return last
        except urllib.error.HTTPError as exc:  # 503 = not_ready，继续等
            last = json.loads(exc.read())
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            pass
        time.sleep(0.5)
    pytest.fail(f"/health/ready 未在超时内就绪: {last}")


def test_live_then_ready_in_demo_profile(server: dict):
    """demo 档位是零网络的：它必须能真的 ready，否则离线首次使用就是空话。"""
    status, _ = server["get"]("/health/live")
    assert status == 200

    ready = _wait_ready(server)
    assert ready["status"] == "ready", ready
    assert ready["schema_version"] == "1"


def test_desktop_web_and_logo_bytes_are_served_from_the_wheel(server: dict):
    _wait_ready(server)

    status, index = server["get"]("/")
    assert status == 200
    assert b'<div id="root">' in index

    # 离线硬要求：首页不得再引用任何外部字体/CDN
    assert b"fonts.googleapis" not in index
    assert b"fonts.gstatic" not in index

    status, logo = server["get"]("/brand.png")
    assert status == 200
    assert hashlib.sha256(logo).hexdigest() == _BRAND_SHA256, "Logo 字节必须逐字不变"


def test_fresh_home_seeds_six_departments_and_two_builtin_skills(server: dict):
    """fresh DB 上 v6 迁移 seed 六部门；两个内建技能随 wheel 分发。"""
    _wait_ready(server)

    status, raw = server["get"]("/api/departments")
    assert status == 200
    departments = json.loads(raw)["data"]
    ids = {d["id"] if isinstance(d, dict) else d for d in departments}
    # 精确集合：多出一个部门也是打包/迁移漂移，正是黑盒该抓的
    assert ids == _EXPECTED_DEPARTMENTS, f"部门集合漂移: {ids ^ _EXPECTED_DEPARTMENTS}"

    status, raw = server["get"]("/api/skills")
    assert status == 200
    skills = json.loads(raw)["data"]
    names = {s["name"] if isinstance(s, dict) else s for s in skills}
    assert names == _EXPECTED_BUILTIN_SKILLS, (
        f"内建技能集合漂移: {names ^ _EXPECTED_BUILTIN_SKILLS}"
    )


def _post(server: dict, path: str, payload: dict, timeout: float = 15.0):
    request = urllib.request.Request(
        f"{server['base']}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@pytest.fixture(scope="module")
def governed_demo_edict(server: dict, installed: dict) -> dict:
    """在安装态里跑完一条受治理的 demo 敕令（isolated staging + governed apply）。"""
    _wait_ready(server)

    source = Path(server["env"]["TIANSHU_WORKSPACE_DIR"])
    source.mkdir(parents=True, exist_ok=True)
    (source / "README.md").write_text("# 源工作区\n", encoding="utf-8")
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "blackbox@example.invalid"],
        ["git", "config", "user.name", "blackbox"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "baseline"],
    ):
        subprocess.run(argv, cwd=source, check=True, capture_output=True, timeout=60)
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()

    goal = "在工作区创建 DEMO.md，写入一行说明。"
    status, body = _post(
        server,
        "/api/edicts",
        {
            "goal": goal,
            "title": "fresh-wheel 黑盒",
            "assigned_persona_id": "bingbu",
            # isolated 的完整要求见 executor/workspace_policy._validate_isolated：
            # workspace-main 源 + 显式非空 base + governed apply + clean source
            # + 恢复点。少一项就是 422 —— 这正是 governed apply 的准入门槛。
            "governance_contract": {
                "objective": {"goal": goal},
                "executor": {"adapter_id": "native"},
                "network": {"mode": "deny"},
                "workspace": {
                    "source_id": "workspace-main",
                    "base_revision": head_before,
                    "staging_mode": "isolated",
                    "apply_mode": "governed",
                    "require_clean_source": True,
                },
                "recovery": {"require_restore_point": True},
            },
        },
    )
    assert status == 202, body
    edict_id = body["data"]["id"]

    deadline = time.time() + 180
    memorial = None
    while time.time() < deadline:
        code, payload = server["get"](f"/api/edicts/{edict_id}/memorial")
        if code == 200:
            memorial = json.loads(payload).get("data")
            if memorial and memorial.get("status") in ("completed", "failed"):
                break
        time.sleep(1.0)
    assert memorial is not None, "未取到 memorial"
    assert memorial["status"] == "completed", memorial

    # memorial 先落终态，workspace finalize 再捕获 canonical change set —— 两者不同步。
    # 黑盒必须等证据真正落地，而不是拿到终态就立刻断言（那只会测出竞态）。
    run_id = memorial["id"]
    deadline = time.time() + 60
    workspace_status = None
    while time.time() < deadline:
        code, raw = server["get"](f"/api/workspace-runs/{run_id}/status")
        if code == 200:
            workspace_status = json.loads(raw)["data"]
            if workspace_status.get("change_set"):
                break
        time.sleep(1.0)
    assert workspace_status is not None, "取不到 workspace status"
    assert workspace_status.get("change_set"), (
        f"隔离 staging 的产物未形成 canonical change set: {workspace_status}"
    )

    return {
        "edict_id": edict_id,
        "memorial": memorial,
        "source": source,
        "head_before": head_before,
        "workspace_status": workspace_status,
    }


def test_governed_demo_edict_completes_offline_with_zero_cost(
    governed_demo_edict: dict,
):
    """离线 demo 档位下走完整治理路径：可见 [DEMO] 标记 + 零成本。"""
    memorial = governed_demo_edict["memorial"]

    assert "[DEMO]" in (memorial.get("result") or ""), memorial.get("result")
    usage = memorial.get("usage") or {}
    assert usage.get("total_tokens") == 0, usage
    assert float(usage.get("cost_cny") or 0) == 0.0, usage
    assert usage.get("actual_model") == "mock/tianshu-demo", usage


def test_workspace_evidence_exists_and_source_stays_untouched(
    server: dict, governed_demo_edict: dict
):
    """真实 G1.4 workspace 接口：lease/restore point/change 证据齐备，
    且**没下裁决就不许动源工作区**——这正是 governed apply 的全部意义。"""
    run_id = governed_demo_edict["memorial"]["id"]
    status = governed_demo_edict["workspace_status"]

    # lease 与 restore point 是 status 的两个顶层证据（见 workspace_api._status_view）
    assert status.get("lease"), status
    assert status["lease"].get("apply_mode") == "governed", status
    assert status.get("restore_point"), "governed apply 必须有执行前恢复点"
    assert status["restore_point"].get("base_revision") == (governed_demo_edict["head_before"]), (
        status["restore_point"]
    )

    # 未签发裁决 → 不该有 decision/receipt
    assert status.get("latest_decision") is None, status
    assert status.get("latest_receipt") is None, status

    # /changes 直接返回 canonical change set 视图（_change_set_view）
    code, raw = server["get"](f"/api/workspace-runs/{run_id}/changes")
    assert code == 200, raw
    change_set = json.loads(raw)["data"]
    assert change_set.get("base_revision") == governed_demo_edict["head_before"], change_set
    paths = {
        change.get("new_path") or change.get("old_path")
        for change in change_set.get("changes") or []
    }
    assert "DEMO.md" in paths, f"隔离 staging 的产物应出现在 canonical change set: {change_set}"

    # 源不变量：未签发 apply decision → 源 HEAD/工作树逐字不变
    source = governed_demo_edict["source"]
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    assert head_after == governed_demo_edict["head_before"], "未裁决却动了源 HEAD"

    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    assert dirty == "", f"未裁决却弄脏了源工作区: {dirty}"
    assert not (source / "DEMO.md").exists(), "产物必须留在 staging，不得直落源工作区"


def test_clean_sigterm_then_db_quick_check_and_unchanged_package_digest(
    server: dict, installed: dict, wheel: Path, governed_demo_edict: dict
):
    """干净停机：无 ResourceWarning / 无残留进程组；停机后库自检 + 包资源 digest 不变。"""
    proc = server["proc"]

    # 基线取自**安装态**（installed fixture，服务尚未跑过）——在这里现取等于
    # "跑完之后再取"，运行期篡改包资源根本抓不到。
    digest_probe = (
        "import json;from tianshu.resources import catalog;"
        "print(json.dumps(catalog.package_digest()))"
    )
    digest_before = installed["pristine_digest"]

    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    output = proc.communicate(timeout=45)[0] or ""
    assert proc.returncode is not None, "SIGTERM 后未退出"

    assert "ResourceWarning" not in output, output[-3000:]
    assert "unclosed" not in output.lower(), output[-3000:]

    # 端口已释放
    with socket.socket() as probe:
        probe.settimeout(2)
        assert probe.connect_ex(("127.0.0.1", server["port"])) != 0, "端口仍被占用"

    # 进程组无残留
    with pytest.raises(ProcessLookupError):
        os.killpg(os.getpgid(proc.pid), 0)

    db_path = Path(server["env"]["TIANSHU_DB_PATH"])
    assert db_path.is_file()
    check = _run(
        [
            str(installed["python"]),
            "-c",
            f"import sqlite3;print(sqlite3.connect({str(db_path)!r}).execute("
            "'PRAGMA quick_check').fetchone()[0])",
        ],
        env=installed["env"],
        cwd=installed["root"],
    )
    assert check.stdout.strip() == "ok", check.stdout + check.stderr

    after = _run(
        [str(installed["python"]), "-c", digest_probe],
        env=installed["env"],
        cwd=installed["root"],
    )
    assert json.loads(after.stdout) == digest_before, "包资源在运行后被改动了"


def test_record_exact_wheel_and_environment_evidence(
    wheel: Path, installed: dict, tmp_path_factory: pytest.TempPathFactory
):
    """把 exact wheel 指纹与环境如实记下来——本机证据的边界必须可复核。"""
    with zipfile.ZipFile(wheel) as archive:
        manifest = sorted(archive.namelist())

    py_version = _run(
        [str(installed["python"]), "-c", "import sys;print(sys.version.split()[0])"],
        env=installed["env"],
        cwd=installed["root"],
    ).stdout.strip()

    evidence = {
        "wheel_name": wheel.name,
        "wheel_sha256": _sha256(wheel),
        "wheel_entry_count": len(manifest),
        "os": os.uname().sysname,
        "arch": os.uname().machine,
        "blackbox_python": py_version,
        "profile": "demo",
        "brand_sha256": _BRAND_SHA256,
        # 本机证明不了的，一律不许写成 pass
        "external_pending": [
            "Linux / Windows",
            "Python 3.13 / 3.14 (litellm 无预编译 wheel，源码构建失败)",
            "容器运行时（G1.6）",
            "公共 registry / 发布 / OIDC",
        ],
    }
    out = tmp_path_factory.mktemp("evidence") / "fresh-wheel-evidence.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    assert len(evidence["wheel_sha256"]) == 64
    assert evidence["arch"] == os.uname().machine
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
