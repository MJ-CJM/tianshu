from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[2]
CHECKER_PATH = ROOT / "scripts" / "check_lean_preview_candidate.py"
SOURCE_COMMIT = "1" * 40
WHEEL_SHA256 = "d" * 64


def _module():
    spec = importlib.util.spec_from_file_location(
        "lean_preview_candidate_checker_evidence", CHECKER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _content_hash(value: dict[str, object]) -> str:
    payload = dict(value)
    payload.pop("content_hash", None)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _payload_hash(files: dict[str, bytes]) -> str:
    hashes = {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}
    return hashlib.sha256(_canonical_bytes(hashes)).hexdigest()


def _gate_logs() -> dict[str, bytes]:
    return {
        "ruff_check": b"All checks passed!\n",
        "ruff_format": b"885 files already formatted\n",
        "mypy": b"Success: no issues found in 132 source files\n",
        "import_linter": (b"483 files, 1754 dependencies.\nContracts: 2 kept, 0 broken.\n"),
        "backend_non_slow": (b"4362 passed, 2 skipped, 29 deselected, 8 warnings in 840.00s\n"),
        "packaging": b"28 passed, 4 warnings in 600.00s\n",
        "web_npm_ci": (
            b"added 465 packages, and audited 466 packages in 2s\nfound 0 vulnerabilities\n"
        ),
        "web_lint": b"35 problems (0 errors, 35 warnings)\n",
        "web_typecheck": (b"> tianshu-web@0.1.0 typecheck\n> tsc --noEmit\n"),
        "web_unit": (b"Test Files  35 passed (35)\nTests  187 passed (187)\n"),
        "web_build": (
            b"\xe2\x9c\x93 3720 modules transformed.\n"
            b"(!) Some chunks are larger than 500 kB\n"
            b"\xe2\x9c\x93 built in 4.1s\n"
        ),
        "web_playwright": b"41 passed (36.3s)\n",
    }


def _write_gate_batch(
    module,
    root: Path,
    *,
    log_overrides: dict[str, bytes] | None = None,
    record_mutation=None,
) -> Path:
    batch = root / "gates" / "batch-1"
    logs = batch / "logs"
    logs.mkdir(parents=True)
    records: dict[str, object] = {}
    payloads = {**_gate_logs(), **(log_overrides or {})}
    for gate_id, command in module.REQUIRED_FINAL_COMMANDS.items():
        raw = payloads[gate_id]
        log_path = logs / f"{gate_id}.log"
        log_path.write_bytes(raw)
        record: dict[str, object] = {
            "command": command,
            "cwd": module.REQUIRED_GATE_CWDS[gate_id],
            "environment": module.required_gate_environment(
                gate_id,
                batch_id="batch-1",
                source_commit=SOURCE_COMMIT,
            ),
            "exit_code": 0,
            "log_ref": f"logs/{gate_id}.log",
            "log_sha256": hashlib.sha256(raw).hexdigest(),
        }
        if record_mutation is not None:
            record_mutation(gate_id, record)
        records[gate_id] = record
    manifest: dict[str, object] = {
        "schema_version": 1,
        "batch_id": "batch-1",
        "source_commit": SOURCE_COMMIT,
        "wheel_sha256": WHEEL_SHA256,
        "commands": records,
    }
    manifest["content_hash"] = _content_hash(manifest)
    path = batch / "manifest.json"
    path.write_bytes(_canonical_bytes(manifest))
    return path


def test_gate_evidence_derives_results_from_hashed_raw_logs(tmp_path: Path) -> None:
    module = _module()
    manifest = _write_gate_batch(module, tmp_path)

    evidence = module.load_gate_evidence(
        manifest,
        artifact_root=tmp_path,
        expected_source_commit=SOURCE_COMMIT,
    )

    assert evidence.content_hash == json.loads(manifest.read_bytes())["content_hash"]
    assert evidence.wheel_sha256 == WHEEL_SHA256
    assert evidence.results["backend_non_slow"] == {
        "command": module.REQUIRED_FINAL_COMMANDS["backend_non_slow"],
        "exit_code": 0,
        "passed": 4362,
        "failed": 0,
        "skipped": 2,
        "deselected": 29,
        "warnings": 8,
        "required_skipped": 0,
        "summary": "4362 passed, 2 skipped, 29 deselected, 8 warnings",
    }
    assert evidence.results["web_playwright"]["passed"] == 41


def test_playwright_rejects_repeated_passed_count_in_one_terminal_summary() -> None:
    module = _module()

    with pytest.raises(module.CandidateGateError, match="exactly 41 passed once"):
        module._derived_gate_result("web_playwright", b"41 passed, 40 passed (36.3s)\n")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("all_zero", "positive passed count"),
        ("fabricated_summary", "fields are not exact"),
        ("missing_log", "missing Gate log"),
        ("tampered_log", "Gate log hash mismatch"),
        ("failed_exit", "required Gate failed"),
        ("playwright_40", "exactly 41 passed"),
        ("packaging_missing_env", "command context"),
        ("packaging_skip", "required Gate skipped"),
        ("playwright_skipped", "0 skipped"),
        ("playwright_duplicate", "exactly one terminal summary"),
        ("playwright_same_line_duplicate", "exactly 41 passed once"),
    ],
)
def test_gate_evidence_rejects_unbacked_or_incomplete_passes(
    tmp_path: Path, mutation: str, message: str
) -> None:
    module = _module()
    overrides = None
    if mutation == "all_zero":
        overrides = {"backend_non_slow": b"0 passed in 1.00s\n"}
    elif mutation == "playwright_40":
        overrides = {"web_playwright": b"40 passed, 1 failed (36.3s)\n"}
    elif mutation == "packaging_skip":
        overrides = {"packaging": b"27 passed, 1 skipped, 4 warnings in 600.00s\n"}
    elif mutation == "playwright_skipped":
        overrides = {"web_playwright": b"41 passed, 1 skipped (36.3s)\n"}
    elif mutation == "playwright_duplicate":
        overrides = {"web_playwright": b"41 passed (36.3s)\n40 passed (20.0s)\n"}
    elif mutation == "playwright_same_line_duplicate":
        overrides = {"web_playwright": b"41 passed, 40 passed (36.3s)\n"}

    def mutate(gate_id: str, record: dict[str, object]) -> None:
        if mutation == "fabricated_summary" and gate_id == "mypy":
            record["summary"] = "passed"
        if mutation == "failed_exit" and gate_id == "mypy":
            record["exit_code"] = 1
        if mutation == "packaging_missing_env" and gate_id == "packaging":
            record["environment"] = {"VIRTUAL_ENV": "unset"}

    manifest = _write_gate_batch(
        module,
        tmp_path,
        log_overrides=overrides,
        record_mutation=mutate,
    )
    if mutation == "missing_log":
        manifest.parent.joinpath("logs/mypy.log").unlink()
    elif mutation == "tampered_log":
        manifest.parent.joinpath("logs/mypy.log").write_text("fabricated pass\n")

    with pytest.raises(module.CandidateGateError, match=message):
        module.load_gate_evidence(
            manifest,
            artifact_root=tmp_path,
            expected_source_commit=SOURCE_COMMIT,
        )


def test_phase_specs_bind_gate_source_and_historical_report_commits() -> None:
    module = _module()
    expected = {
        "s1_g1_5": (
            "bbf84451a40f8f3450e080c939c82fba52428271",
            "8c2303df525b05a69d1a6902c83b06c5fd50102d",
        ),
        "s2_lean": (
            "bbf672e560ecd2c793a1a80d0cc262b41550a4db",
            "66e59943b91bc708344c69b895eaa8cfc3298721",
        ),
        "s3_core": (
            "60d3c45b836de44b132dba186e5c9a3672592ea3",
            "2eb20d6dfd39b172f438c90aee5eaee507d8a227",
        ),
        "s4_automation": (
            "303787916f1004362c3f250c07a8de179aa0885d",
            "303787916f1004362c3f250c07a8de179aa0885d",
        ),
        "s5_lean_core": (
            "f6777b435631ab3d5fa1aeac1a96cdbf2c424774",
            "f6777b435631ab3d5fa1aeac1a96cdbf2c424774",
        ),
    }
    assert {
        phase_id: (spec.gate_source_commit, spec.report_commit)
        for phase_id, spec in module.PHASE_SPECS.items()
    } == expected


def test_phase_inputs_reject_wrong_report_commit_and_historical_bytes(monkeypatch) -> None:
    module = _module()
    wrong = replace(module.PHASE_SPECS["s3_core"], report_commit="2" * 40)
    monkeypatch.setitem(module.PHASE_SPECS, "s3_core", wrong)
    with pytest.raises(module.CandidateGateError, match="historical phase report"):
        module._phase_inputs(ROOT)

    monkeypatch.setitem(
        module.PHASE_SPECS,
        "s3_core",
        replace(wrong, report_commit="2eb20d6dfd39b172f438c90aee5eaee507d8a227"),
    )
    monkeypatch.setattr(
        module.GitBackend,
        "read_file_at_commit",
        lambda *_args, **_kwargs: b"not the retained report\n",
        raising=False,
    )
    with pytest.raises(module.CandidateGateError, match="historical phase report"):
        module._phase_inputs(ROOT)


def _tar_member(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    archive.addfile(info, io.BytesIO(payload))


def _write_distributions(
    root: Path,
    *,
    hidden_manifest: bool = False,
    second_root: bool = False,
    extra_source: bool = False,
    unsafe_symlink: bool = False,
    manifest_payload: bytes = b"{}",
) -> tuple[Path, Path]:
    sdist = root / "tianshu-0.4.2.tar.gz"
    wheel = root / "tianshu-0.4.2-py3-none-any.whl"
    manifest_name = (
        "src/tianshu/web/static/.vite/manifest.json"
        if hidden_manifest
        else "src/tianshu/web/static/manifest.json"
    )
    with tarfile.open(sdist, "w:gz") as archive:
        _tar_member(archive, "tianshu-0.4.2/pyproject.toml", b"[build-system]\n")
        _tar_member(archive, "tianshu-0.4.2/src/tianshu/__init__.py", b"VERSION = 1\n")
        _tar_member(archive, f"tianshu-0.4.2/{manifest_name}", manifest_payload)
        if extra_source:
            _tar_member(
                archive,
                "tianshu-0.4.2/src/tianshu/uncommitted.py",
                b"UNTRACKED = True\n",
            )
        if second_root:
            _tar_member(archive, "other-root/README.md", b"unexpected\n")
        if unsafe_symlink:
            link = tarfile.TarInfo("tianshu-0.4.2/src/tianshu/escape")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../../../outside"
            archive.addfile(link)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("tianshu/__init__.py", b"VERSION = 1\n")
        wheel_manifest = manifest_name.removeprefix("src/")
        archive.writestr(wheel_manifest, manifest_payload)
        if extra_source:
            archive.writestr("tianshu/uncommitted.py", b"UNTRACKED = True\n")
        archive.writestr("tianshu-0.4.2.dist-info/WHEEL", b"Wheel-Version: 1.0\n")
    return sdist, wheel


def _write_build_provenance(
    root: Path,
    sdist: Path,
    wheel: Path,
    *,
    manifest_payload: bytes = b"{}",
) -> Path:
    batch = root / "builds" / "batch-1"
    logs = batch / "logs"
    logs.mkdir(parents=True)
    sdist_log = b"Successfully built tianshu-0.4.2.tar.gz\n"
    wheel_log = b"Successfully built tianshu-0.4.2-py3-none-any.whl\n"
    web_install_log = b"added 1 package\nfound 0 vulnerabilities\n"
    web_build_log = b"vite build\nbuilt in 1ms\n"
    (logs / "sdist.log").write_bytes(sdist_log)
    (logs / "wheel.log").write_bytes(wheel_log)
    (logs / "web_npm_ci.log").write_bytes(web_install_log)
    (logs / "web_build.log").write_bytes(web_build_log)
    sdist_hash = hashlib.sha256(sdist.read_bytes()).hexdigest()
    sdist_root = "tianshu-0.4.2"
    payload: dict[str, object] = {
        "schema_version": 1,
        "source_commit": SOURCE_COMMIT,
        "python_version": "3.12.12",
        "frontend": {"name": "build", "version": "1.5.0"},
        "web": {
            "source_sha256": _payload_hash({"web/package.json": b"{}"}),
            "static_sha256": _payload_hash({"manifest.json": manifest_payload}),
            "npm_ci": {
                "command": "npm ci",
                "cwd": "web",
                "exit_code": 0,
                "log_ref": "logs/web_npm_ci.log",
                "log_sha256": hashlib.sha256(web_install_log).hexdigest(),
            },
            "build": {
                "command": "npm run build",
                "cwd": "web",
                "exit_code": 0,
                "log_ref": "logs/web_build.log",
                "log_sha256": hashlib.sha256(web_build_log).hexdigest(),
            },
        },
        "sdist": {
            "command": "python -m build --sdist --outdir dist/lean-preview-candidate",
            "cwd": ".",
            "exit_code": 0,
            "log_ref": "logs/sdist.log",
            "log_sha256": hashlib.sha256(sdist_log).hexdigest(),
            "sha256": sdist_hash,
        },
        "wheel": {
            "command": "python -m build --wheel --outdir ../../../from-sdist",
            "cwd": f"dist/lean-preview-candidate/extracted/{sdist_hash}/{sdist_root}",
            "exit_code": 0,
            "log_ref": "logs/wheel.log",
            "log_sha256": hashlib.sha256(wheel_log).hexdigest(),
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "source_sdist_sha256": sdist_hash,
        },
    }
    payload["content_hash"] = _content_hash(payload)
    path = batch / "provenance.json"
    path.write_bytes(_canonical_bytes(payload))
    return path


def test_build_provenance_binds_source_sdist_wheel_and_visible_web_manifest(
    tmp_path: Path,
) -> None:
    module = _module()
    sdist, wheel = _write_distributions(tmp_path)
    provenance = _write_build_provenance(tmp_path, sdist, wheel)

    result = module.verify_build_provenance(
        provenance,
        artifact_root=tmp_path,
        expected_source_commit=SOURCE_COMMIT,
        sdist_path=sdist,
        wheel_path=wheel,
        tracked_source_files={
            "pyproject.toml": b"[build-system]\n",
            "src/tianshu/__init__.py": b"VERSION = 1\n",
            "web/package.json": b"{}",
        },
        rebuilt_web_static={"manifest.json": b"{}"},
    )

    assert result.content_hash == json.loads(provenance.read_bytes())["content_hash"]
    assert result.wheel_sha256 == hashlib.sha256(wheel.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("provenance", "provenance hash"),
        ("wheel", "Wheel package payload"),
        ("wheel_extra", "Wheel package payload"),
        ("sdist_extra_source", "committed source"),
        ("sdist_symlink", "unsafe member"),
        ("build_command", "build command"),
        ("build_cwd", "build cwd"),
        ("build_exit", "build failed"),
        ("build_error_log", "records failure"),
        ("python_version", "Python 3.12"),
        ("evil_package", "installable payload"),
        ("hidden_manifest", "visible manifest"),
        ("second_root", "single root"),
        ("static_rebuilt_mismatch", "rebuilt committed Web source"),
    ],
)
def test_build_provenance_rejects_replaced_or_untraceable_artifacts(
    tmp_path: Path, mutation: str, message: str
) -> None:
    module = _module()
    manifest_payload = b'{"tampered":true}' if mutation == "static_rebuilt_mismatch" else b"{}"
    sdist, wheel = _write_distributions(
        tmp_path,
        hidden_manifest=mutation == "hidden_manifest",
        second_root=mutation == "second_root",
        extra_source=mutation == "sdist_extra_source",
        unsafe_symlink=mutation == "sdist_symlink",
        manifest_payload=manifest_payload,
    )
    provenance = _write_build_provenance(
        tmp_path,
        sdist,
        wheel,
        manifest_payload=manifest_payload,
    )
    if mutation == "provenance":
        payload = json.loads(provenance.read_bytes())
        payload["frontend"]["version"] = "9.9.9"
        provenance.write_bytes(_canonical_bytes(payload))
    elif mutation == "wheel":
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("tianshu/__init__.py", b"REPLACED = True\n")
            archive.writestr("tianshu/web/static/manifest.json", b"{}")
    elif mutation == "wheel_extra":
        with zipfile.ZipFile(wheel, "a") as archive:
            archive.writestr("tianshu/uncommitted.py", b"UNTRACKED = True\n")
        payload = json.loads(provenance.read_bytes())
        payload["wheel"]["sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        payload["content_hash"] = _content_hash(payload)
        provenance.write_bytes(_canonical_bytes(payload))
    elif mutation == "build_command":
        payload = json.loads(provenance.read_bytes())
        payload["wheel"]["command"] = "python -m build --wheel ."
        payload["content_hash"] = _content_hash(payload)
        provenance.write_bytes(_canonical_bytes(payload))
    elif mutation == "build_cwd":
        payload = json.loads(provenance.read_bytes())
        payload["wheel"]["cwd"] = "dist/lean-preview-candidate/extracted/unbound"
        payload["content_hash"] = _content_hash(payload)
        provenance.write_bytes(_canonical_bytes(payload))
    elif mutation == "build_exit":
        payload = json.loads(provenance.read_bytes())
        payload["sdist"]["exit_code"] = 1
        payload["content_hash"] = _content_hash(payload)
        provenance.write_bytes(_canonical_bytes(payload))
    elif mutation == "build_error_log":
        failed_log = b"Successfully built tianshu-0.4.2.tar.gz\nERROR: build failed\n"
        provenance.parent.joinpath("logs/sdist.log").write_bytes(failed_log)
        payload = json.loads(provenance.read_bytes())
        payload["sdist"]["log_sha256"] = hashlib.sha256(failed_log).hexdigest()
        payload["content_hash"] = _content_hash(payload)
        provenance.write_bytes(_canonical_bytes(payload))
    elif mutation == "python_version":
        payload = json.loads(provenance.read_bytes())
        payload["python_version"] = "3.13.0"
        payload["content_hash"] = _content_hash(payload)
        provenance.write_bytes(_canonical_bytes(payload))
    elif mutation == "evil_package":
        with zipfile.ZipFile(wheel, "a") as archive:
            archive.writestr("evil/__init__.py", b"EVIL = True\n")
        payload = json.loads(provenance.read_bytes())
        payload["wheel"]["sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        payload["content_hash"] = _content_hash(payload)
        provenance.write_bytes(_canonical_bytes(payload))
    with pytest.raises(module.CandidateGateError, match=message):
        module.verify_build_provenance(
            provenance,
            artifact_root=tmp_path,
            expected_source_commit=SOURCE_COMMIT,
            sdist_path=sdist,
            wheel_path=wheel,
            tracked_source_files={
                "pyproject.toml": b"[build-system]\n",
                "src/tianshu/__init__.py": b"VERSION = 1\n",
                "web/package.json": b"{}",
            },
            rebuilt_web_static={"manifest.json": b"{}"},
        )


@pytest.mark.parametrize("different_gate_wheel", [False, True])
def test_candidate_assembly_consumes_one_wheel_identity_across_gate_and_build(
    tmp_path: Path, monkeypatch, different_gate_wheel: bool
) -> None:
    module = _module()
    monkeypatch.setattr(module, "ROOT", tmp_path)

    gate_path = tmp_path / "docs/cc-fable-v1/evidence/gates/batch-1/manifest.json"
    provenance_path = tmp_path / "docs/cc-fable-v1/evidence/builds/batch-1/provenance.json"
    demo_path = tmp_path / "docs/cc-fable-v1/evidence/lean-preview/batch-1/demo-report.json"
    for path in (gate_path, provenance_path, demo_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("tracked evidence\n", encoding="utf-8")
    legacy = tmp_path / "dist/lean-preview-candidate/final-gates.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("fabricated legacy summaries\n", encoding="utf-8")

    sdist, wheel = _write_distributions(tmp_path / "dist/lean-preview-candidate")
    target_wheel = tmp_path / "dist/lean-preview-candidate/from-sdist" / wheel.name
    target_wheel.parent.mkdir()
    wheel.rename(target_wheel)
    wheel = target_wheel
    capability = tmp_path / "docs/launch/capability-matrix.md"
    capability.parent.mkdir(parents=True)
    capability.write_text(
        "desktop Web user_approval_pending VoiceOver external_pending full G4 "
        "full G5 remote MCP open stdio MCP disabled publication_status "
        "not_authorized\n",
        encoding="utf-8",
    )

    class Backend:
        def resolve_commit(self, _location, _ref):
            return SOURCE_COMMIT

        def worktree_status_paths(self, _location):
            return (
                gate_path.relative_to(tmp_path).as_posix(),
                provenance_path.relative_to(tmp_path).as_posix(),
                demo_path.relative_to(tmp_path).as_posix(),
            )

        def list_log(self, _location):
            return tuple(
                SimpleNamespace(sha=commit)
                for spec in module.PHASE_SPECS.values()
                for commit in (spec.gate_source_commit, spec.report_commit)
            )

        def tracked_paths(self, _location, _prefix):
            return ()

        def list_files_at_commit(self, _location, _sha):
            return ("pyproject.toml", "src/tianshu/__init__.py")

        def read_file_at_commit(self, _location, _sha, relative):
            return {
                "pyproject.toml": b"[build-system]\n",
                "src/tianshu/__init__.py": b"VERSION = 1\n",
            }[relative]

    monkeypatch.setattr(module, "GitBackend", Backend)
    results = {
        gate_id: module._derived_gate_result(gate_id, raw) for gate_id, raw in _gate_logs().items()
    }
    gate_hash = "a" * 64
    provenance_hash = "b" * 64
    candidate_wheel_sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()
    gate_wheel_sha256 = (
        hashlib.sha256(b"different Gate Wheel").hexdigest()
        if different_gate_wheel
        else candidate_wheel_sha256
    )
    monkeypatch.setattr(
        module,
        "load_gate_evidence",
        lambda path, **_kwargs: (
            SimpleNamespace(
                content_hash=gate_hash,
                results=results,
                wheel_sha256=gate_wheel_sha256,
            )
            if path == gate_path
            else pytest.fail("assembly loaded an unexpected Gate source")
        ),
    )
    provenance_calls: list[dict[str, object]] = []

    def verify_provenance(path, **kwargs):
        assert path == provenance_path
        provenance_calls.append(kwargs)
        return SimpleNamespace(
            content_hash=provenance_hash,
            wheel_sha256=candidate_wheel_sha256,
        )

    monkeypatch.setattr(module, "verify_build_provenance", verify_provenance)
    monkeypatch.setattr(
        module,
        "verify_demo_evidence",
        lambda *_args, **_kwargs: {
            "source_commit": SOURCE_COMMIT,
            "wheel_sha256": candidate_wheel_sha256,
            "fixture": False,
            "steps": [{"status": "passed"}] * 13,
            "content_hash": "c" * 64,
        },
    )
    phases = {
        phase_id: module.PhaseReportInput(
            phase_id=phase_id,
            gate_id=spec.gate_id,
            report_ref=spec.report_ref,
            report_bytes=spec.pass_marker.encode(),
            gate_source_commit=spec.gate_source_commit,
            report_commit=spec.report_commit,
        )
        for phase_id, spec in module.PHASE_SPECS.items()
    }
    monkeypatch.setattr(module, "_phase_inputs", lambda _root: phases)
    monkeypatch.setattr(module, "_parse_screenshot_manifest", lambda _root: ({}, {}))
    monkeypatch.setattr(module, "_deferred_ids", lambda _root: module.REQUIRED_DEFERRED_WORK_IDS)
    monkeypatch.setattr(module, "verify_candidate_report", lambda *_args, **_kwargs: None)
    assert not hasattr(module, "_load_gate_manifest")

    output = tmp_path / "candidate.json"
    report = tmp_path / "candidate.md"
    if different_gate_wheel:
        with pytest.raises(module.CandidateGateError, match="Wheel identity"):
            module.assemble_candidate(
                output=output,
                report=report,
                gate_evidence=gate_path,
                build_provenance=provenance_path,
                demo_report=demo_path,
            )
        assert not output.exists()
        return
    module.assemble_candidate(
        output=output,
        report=report,
        gate_evidence=gate_path,
        build_provenance=provenance_path,
        demo_report=demo_path,
    )

    candidate = json.loads(output.read_bytes())
    assert candidate["gate_evidence_ref"] == gate_path.relative_to(tmp_path).as_posix()
    assert candidate["gate_evidence_hash"] == hashlib.sha256(gate_path.read_bytes()).hexdigest()
    assert candidate["build_provenance_ref"] == provenance_path.relative_to(tmp_path).as_posix()
    assert (
        candidate["build_provenance_hash"]
        == hashlib.sha256(provenance_path.read_bytes()).hexdigest()
    )
    assert provenance_calls[0]["tracked_source_files"] == {
        "pyproject.toml": b"[build-system]\n",
        "src/tianshu/__init__.py": b"VERSION = 1\n",
    }
