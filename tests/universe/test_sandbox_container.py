"""Tests for ContainerRunner —— 运行时探测、命令构造、可用/降级/超时/异常路径。

全程打桩 shutil.which / subprocess.run，绝不真的调用 docker，也不联网。
"""

from __future__ import annotations

import subprocess

import pytest

from tianshu.universe.sandbox_container import ContainerRunner


def _which_factory(available: set[str]):
    """返回一个假 shutil.which：命中 available 集合的名字才给路径，否则 None。"""

    def _which(name: str, *args, **kwargs) -> str | None:
        return f"/usr/bin/{name}" if name in available else None

    return _which


# --- detect_runtime / is_available -------------------------------------------------


def test_detect_runtime_prefers_docker(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("shutil.which", _which_factory({"docker", "container"}))
    assert ContainerRunner().detect_runtime() == "docker"


def test_detect_runtime_falls_back_to_apple_container(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("shutil.which", _which_factory({"container"}))
    assert ContainerRunner().detect_runtime() == "container"


def test_detect_runtime_none_when_absent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("shutil.which", _which_factory(set()))
    runner = ContainerRunner()
    assert runner.detect_runtime() is None
    assert runner.is_available() is False


def test_is_available_true_when_docker_present(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("shutil.which", _which_factory({"docker"}))
    assert ContainerRunner().is_available() is True


# --- build_command -----------------------------------------------------------------


def test_build_command_has_isolation_flags(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("shutil.which", _which_factory({"docker"}))
    cmd = ContainerRunner().build_command(
        ["python", "-m", "pytest"],
        "/host/worktree",
        image="python:3.12-slim",
        memory="512m",
        cpus="1.0",
    )
    joined = " ".join(cmd)
    assert cmd[0] == "docker"
    assert cmd[1:3] == ["run", "--rm"]
    assert "--network none" in joined  # 断网
    assert "--read-only" in cmd  # 只读根文件系统
    assert "--memory" in cmd and "512m" in cmd  # 内存限额
    assert "--cpus" in cmd and "1.0" in cmd  # CPU 限额
    assert "/host/worktree:/workspace:ro" in cmd  # worktree 只读挂载
    assert cmd[-3:] == ["python", "-m", "pytest"]  # 用户命令追加在末尾
    assert "python:3.12-slim" in cmd  # 镜像


def test_build_command_toggles_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("shutil.which", _which_factory({"docker"}))
    cmd = ContainerRunner().build_command(["echo", "hi"], "/wt", readonly=False, network_none=False)
    assert "--read-only" not in cmd
    assert "--network" not in cmd
    # 挂载仍是只读（:ro 与 --read-only 是两回事，前者不受 readonly 开关影响）
    assert "/wt:/workspace:ro" in cmd


def test_build_command_defaults_to_docker_shape_without_runtime(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("shutil.which", _which_factory(set()))
    cmd = ContainerRunner().build_command(["true"], "/wt")
    assert cmd[0] == "docker"  # 无运行时时按首选 docker 形状构造


# --- run: 降级 / 可用 / 超时 / 异常 ------------------------------------------------


def test_run_degrades_without_runtime(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("shutil.which", _which_factory(set()))

    def _boom(*args, **kwargs):  # subprocess.run 绝不该被调用
        raise AssertionError("subprocess.run should not run when degraded")

    monkeypatch.setattr(subprocess, "run", _boom)
    result = ContainerRunner().run(["true"], "/wt")
    assert result == {"degraded": True, "reason": "no_container_runtime"}


def test_run_available_returns_structured_result(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("shutil.which", _which_factory({"docker"}))
    captured: dict[str, object] = {}

    def _fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(argv, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    result = ContainerRunner().run(["python", "-c", "print(1)"], "/wt", timeout=42)
    assert result == {
        "degraded": False,
        "returncode": 0,
        "stdout": "ok",
        "stderr": "",
    }
    assert captured["argv"][0] == "docker"  # 走的是容器命令
    assert captured["timeout"] == 42  # 超时透传到 subprocess.run


def test_run_captures_timeout(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("shutil.which", _which_factory({"docker"}))

    def _timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1, output=b"partial", stderr=None)

    monkeypatch.setattr(subprocess, "run", _timeout)
    result = ContainerRunner().run(["sleep", "99"], "/wt", timeout=1)
    assert result["degraded"] is False
    assert result["timed_out"] is True
    assert result["returncode"] is None
    assert result["stdout"] == "partial"  # bytes 被 _coerce 成 str
    assert result["stderr"] == ""  # None → ""


def test_run_captures_exec_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("shutil.which", _which_factory({"docker"}))

    def _oserror(argv, **kwargs):
        raise OSError("exec format error")

    monkeypatch.setattr(subprocess, "run", _oserror)
    result = ContainerRunner().run(["true"], "/wt")
    assert result["degraded"] is False
    assert result["error"] is True
    assert result["returncode"] is None
    assert "exec format error" in str(result["stderr"])
