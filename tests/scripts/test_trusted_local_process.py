from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parents[2]
HELPER_PATH = ROOT / "scripts" / "_trusted_local_process.py"


def _module():
    assert HELPER_PATH.exists(), "trusted-local process helper is missing"
    spec = importlib.util.spec_from_file_location("trusted_local_process", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_preserves_argv_cwd_env_output_and_nonzero_exit(tmp_path: Path) -> None:
    module = _module()
    environment = dict(os.environ)
    environment["TIANSHU_PROCESS_TEST"] = "expected"
    program = (
        "import json, os, pathlib, sys; "
        "print(json.dumps({'argv': sys.argv[1:], 'cwd': str(pathlib.Path.cwd()), "
        "'env': os.environ['TIANSHU_PROCESS_TEST']})); "
        "print('stderr bytes', file=sys.stderr); "
        "raise SystemExit(7)"
    )

    result = module.run_trusted_local_process(
        [sys.executable, "-c", program, "one", "two"],
        cwd=tmp_path,
        env=environment,
    )

    assert json.loads(result.stdout) == {
        "argv": ["one", "two"],
        "cwd": str(tmp_path),
        "env": "expected",
    }
    assert result.stderr == b"stderr bytes\n"
    assert result.output == result.stdout + result.stderr
    assert result.returncode == 7


def test_runner_uses_explicit_trusted_local_host_unrestricted_backend(
    monkeypatch,
) -> None:
    module = _module()
    calls: list[dict[str, object]] = []

    class Process:
        returncode = 0

        async def communicate(self):
            return b"stdout\n", b"stderr\n"

    class Backend:
        async def spawn(self, **kwargs):
            calls.append(kwargs)
            spawned = SimpleNamespace(process=Process())
            kwargs["on_spawned"](spawned)
            return spawned

    monkeypatch.setattr(module, "AsyncioProcessBackend", Backend)
    monkeypatch.setenv("TIANSHU_INHERITED_PROCESS_TEST", "inherited")

    result = module.run_trusted_local_process(["tool", "arg"], cwd=Path("/tmp"))

    assert len(calls) == 1
    call = calls[0]
    assert call["argv"] == ("tool", "arg")
    assert call["cwd"] == Path("/tmp")
    assert call["env"]["TIANSHU_INHERITED_PROCESS_TEST"] == "inherited"
    assert call["network"].model_dump() == {
        "mode": "unrestricted",
        "allowed_hosts": (),
        "enforcement_required": False,
    }
    assert call["sandbox"].model_dump() == {
        "trust_level": "trusted-local",
        "mode": "host",
        "allow_host": True,
        "backend": None,
    }
    assert call["stdin_mode"] == "null"
    assert callable(call["on_spawned"])
    assert result.output == b"stdout\nstderr\n"
