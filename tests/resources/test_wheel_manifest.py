"""Wheel 归档 manifest 检查：安装制品必须自包含全部不可变资源。

构建真实 wheel（子进程，标 slow）并直接检查 ZIP 名单——源树存在不算数，
归档内容才是验收 oracle。

web/static 是 vite 产物且被 gitignore：这里**硬断言**它在 wheel 里。此前写成
"存在则断言、缺席则 skip"，而干净检出里它恰恰不存在——于是 CI 与其他开发者
构建出的、没有界面的 wheel 一路绿灯。现在构建后端对缺 Web 载荷 fail-closed
（build_backend/tianshu_build.py），本文件的 wheel fixture 因此在前端未构建时
直接失败并给出构建指引，而不是悄悄跳过。
"""

import hashlib
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BRAND_SHA256 = "3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799"
_WEB_STATIC = _REPO_ROOT / "src" / "tianshu" / "web" / "static"

pytestmark = pytest.mark.slow


def _build_wheel(out_dir: Path) -> Path:
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"wheel 构建失败（前端未构建时这是预期行为，见 scripts/build_release.sh）：\n"
        f"{result.stderr[-2000:]}"
    )
    wheels = sorted(out_dir.glob("tianshu-*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    return wheels[0]


@pytest.fixture(scope="session")
def wheel_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build_wheel(tmp_path_factory.mktemp("wheel-out"))


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


def test_wheel_contains_web_static_with_exact_brand_bytes(
    wheel_names: frozenset[str], wheel_path: Path
) -> None:
    """硬断言：没有 Web 载荷的 wheel 就是个没有界面的发行物，不许静默放行。"""
    static_files = _family(wheel_names, "tianshu/web/static/")
    assert "tianshu/web/static/index.html" in static_files
    assert "tianshu/web/static/brand.png" in static_files
    with zipfile.ZipFile(wheel_path) as archive:
        brand = archive.read("tianshu/web/static/brand.png")
    assert hashlib.sha256(brand).hexdigest() == _BRAND_SHA256


def test_wheel_web_payload_matches_source_tree_exactly(wheel_names: frozenset[str]) -> None:
    """wheel 的 Web 载荷 == 当次前端产物，不多不少。

    setuptools 的 ``build/`` 暂存树从不自清：某个前端 chunk 被打包过一次就永远
    留在那儿，于是同一份源码会构建出不同的 wheel（实测混进过 3 个上一轮的孤儿
    chunk，约 933KB）。构建后端现在每次构建前清空暂存树——这条断言锁定它。
    """
    packaged = {
        name[len("tianshu/web/static/") :]
        for name in _family(wheel_names, "tianshu/web/static/")
        if not name.endswith("/")
    }
    on_disk = {
        str(path.relative_to(_WEB_STATIC))
        for path in _WEB_STATIC.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert packaged == on_disk, (
        "wheel 的 Web 载荷与源树不一致——多出的是陈旧暂存产物，缺失的是漏打包"
    )


def test_wheel_build_is_reproducible_from_the_same_source(
    wheel_names: frozenset[str], tmp_path: Path
) -> None:
    """同一份源码两次构建 → 同一份 manifest（否则发行物 SHA 不可复现）。"""
    second = _build_wheel(tmp_path / "again")
    with zipfile.ZipFile(second) as archive:
        assert frozenset(archive.namelist()) == wheel_names


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


def _build_sdist(out_dir: Path) -> Path:
    result = subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(out_dir)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"sdist 构建失败：\n{result.stderr[-2000:]}"
    sdists = sorted(out_dir.glob("tianshu-*.tar.gz"))
    assert len(sdists) == 1, f"expected exactly one sdist, got {sdists}"
    return sdists[0]


def test_sdist_ships_the_in_tree_build_backend(tmp_path: Path) -> None:
    """sdist 必须带上 build_backend/。

    它是 pyproject 的 backend-path 指向的 in-tree PEP 517 后端；setuptools 不会
    自动收录 backend-path 目录。缺了它，从 sdist 构建 wheel 会 ModuleNotFoundError:
    tianshu_build —— 即 PEP 517 sdist 构建与"从 sdist 安装"都会断掉，而
    只跑 wheel 路径的 CI 永远看不见。
    """
    sdist = _build_sdist(tmp_path / "sdist-out")
    with tarfile.open(sdist) as archive:
        names = {Path(*Path(name).parts[1:]).as_posix() for name in archive.getnames()}
    assert "build_backend/tianshu_build.py" in names, (
        "sdist 缺少 in-tree 构建后端——从 sdist 构建/安装会直接失败"
    )


def test_wheel_built_from_sdist_matches_direct_build(tmp_path: Path) -> None:
    """从 sdist 构建出的 wheel 必须与直接构建的一致（发行链路两条路等价）。"""
    sdist = _build_sdist(tmp_path / "sdist-src")
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(sdist) as archive:
        archive.extractall(extracted, filter="data")
    source_roots = [path for path in extracted.iterdir() if path.is_dir()]
    assert len(source_roots) == 1
    out = tmp_path / "from-sdist"
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
        cwd=source_roots[0],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, f"从 sdist 构建 wheel 失败：\n{result.stderr[-2000:]}"
    from_sdist = sorted(out.glob("tianshu-*.whl"))
    assert len(from_sdist) == 1
    direct = _build_wheel(tmp_path / "direct")

    def _payload(path: Path) -> dict[str, str]:
        with zipfile.ZipFile(path) as archive:
            return {
                name: hashlib.sha256(archive.read(name)).hexdigest()
                for name in archive.namelist()
                if not name.endswith(("RECORD", "WHEEL"))  # 构建器指纹，不属载荷
            }

    assert _payload(from_sdist[0]) == _payload(direct), "两条发行链路产出的 wheel 载荷不一致"
