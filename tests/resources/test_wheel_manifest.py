"""Wheel 归档 manifest 检查：安装制品必须自包含全部不可变资源。

构建真实 wheel（子进程，标 slow）并直接检查 ZIP 名单——源树存在不算数，
归档内容才是验收 oracle。web/static 为 vite 构建产物（gitignore），仅在
已构建时强制断言；发布链路的完整性由 S1.5 fresh-wheel 黑盒兜底。
"""

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BRAND_SHA256 = "3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799"
_WEB_STATIC = _REPO_ROOT / "src" / "tianshu" / "web" / "static"

pytestmark = pytest.mark.slow


@pytest.fixture(scope="session")
def wheel_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out_dir = tmp_path_factory.mktemp("wheel-out")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    wheels = sorted(out_dir.glob("tianshu-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    return wheels[0]


@pytest.fixture(scope="session")
def wheel_names(wheel_path: Path) -> frozenset[str]:
    with zipfile.ZipFile(wheel_path) as archive:
        return frozenset(archive.namelist())


def _family(names: frozenset[str], prefix: str) -> set[str]:
    return {name for name in names if name.startswith(prefix)}


def test_wheel_contains_six_persona_departments_and_court(wheel_names: frozenset[str]) -> None:
    persona_files = _family(wheel_names, "tianshu/resources/personas/")
    departments = {name.split("/")[3] for name in persona_files if name.count("/") >= 4}
    assert departments == {
        "bingbu",
        "ducha",
        "hubu",
        "neige",
        "tongzheng",
        "wenyuan",
        "court",
    }
    assert len(persona_files) == 20


def test_wheel_contains_395_persona_templates_plus_sources(wheel_names: frozenset[str]) -> None:
    template_files = _family(wheel_names, "tianshu/resources/persona_templates/")
    markdown = {name for name in template_files if name.endswith(".md")}
    assert "tianshu/resources/persona_templates/SOURCES.md" in template_files
    en = {name for name in markdown if "/en/" in name}
    zh = {name for name in markdown if "/zh/" in name}
    assert len(en) == 191
    assert len(zh) == 204
    assert len(template_files) == 396


def test_wheel_contains_exactly_two_builtin_skills(wheel_names: frozenset[str]) -> None:
    skill_files = _family(wheel_names, "tianshu/skills/builtin/")
    assert skill_files == {
        "tianshu/skills/builtin/file-ops/SKILL.md",
        "tianshu/skills/builtin/shell/SKILL.md",
    }


def test_wheel_contains_executor_markdown_templates(wheel_names: frozenset[str]) -> None:
    templates = {
        name for name in _family(wheel_names, "tianshu/executor/templates/") if name.endswith(".md")
    }
    assert templates == {
        "tianshu/executor/templates/edict/completion_audit.md",
        "tianshu/executor/templates/edict/continuation.md",
        "tianshu/executor/templates/edict/wind_down.md",
    }


def test_wheel_contains_license_py_typed_and_metadata(
    wheel_names: frozenset[str], wheel_path: Path
) -> None:
    assert "tianshu/py.typed" in wheel_names
    assert "tianshu/resources/LICENSE" in wheel_names
    dist_info = {name for name in wheel_names if ".dist-info/" in name}
    assert any(name.endswith("/METADATA") for name in dist_info)
    assert any(name.endswith("licenses/LICENSE") for name in dist_info)
    version = wheel_path.name.split("-")[1]
    from tianshu import __version__

    assert version == __version__


def test_wheel_has_no_stray_repo_root_resource_members(wheel_names: frozenset[str]) -> None:
    assert not _family(wheel_names, "personas/")
    assert not _family(wheel_names, "templates/")
    assert not any(name.endswith(".pyc") for name in wheel_names)
    assert not any("__pycache__" in name for name in wheel_names)


@pytest.mark.skipif(
    not (_WEB_STATIC / "index.html").exists(),
    reason="vite build output missing; run `npm run build` in web/ — S1.5 黑盒强制覆盖此路径",
)
def test_wheel_contains_web_static_with_exact_brand_bytes(
    wheel_names: frozenset[str], wheel_path: Path
) -> None:
    static_files = _family(wheel_names, "tianshu/web/static/")
    assert "tianshu/web/static/index.html" in static_files
    assert "tianshu/web/static/brand.png" in static_files
    with zipfile.ZipFile(wheel_path) as archive:
        brand = archive.read("tianshu/web/static/brand.png")
    assert hashlib.sha256(brand).hexdigest() == _BRAND_SHA256


def test_source_brand_png_is_frozen_bytes() -> None:
    brand = (_REPO_ROOT / "web" / "public" / "brand.png").read_bytes()
    assert hashlib.sha256(brand).hexdigest() == _BRAND_SHA256


def test_wheel_import_smoke_from_repo_external_cwd(wheel_path: Path, tmp_path: Path) -> None:
    """安装态语义的最小烟测：外部 cwd + 清 PYTHONPATH 下 zip 内容可枚举。

    真正的 fresh-venv 安装黑盒属 S1.5；此处仅锁定构建产物可被独部环境读取。
    """
    script = (
        "import zipfile,sys;"
        f"names=zipfile.ZipFile({str(wheel_path)!r}).namelist();"
        "assert 'tianshu/resources/catalog.py' in names, 'catalog module missing from wheel'"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
