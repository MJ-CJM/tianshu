"""In-tree PEP 517 后端：可复现的 wheel + 强制的 Web 载荷。

裸 setuptools 后端对本项目不安全，两个事实：

1. ``build/`` 是 setuptools 的暂存树，**从不清理**。某个文件被打包过一次就一直
   留在那儿，于是后续 ``uv build`` 会把源树里早已不存在的资产打进去——同一份源码
   构建出不同的 wheel（实测：上一轮前端产物的 3 个孤儿 chunk 混进了发行物）。
2. ``src/tianshu/web/static/`` 是 vite 产物且被 gitignore。干净检出构建出的 wheel
   **零 Web 文件**，装上去 ``app.py`` 会 mount 一个空目录——一个静默没有界面的发行物。

所以：每次构建前清空暂存树；没有 Web 载荷就拒绝出 wheel。editable 安装（开发回路）
不受影响——它本就从源树直读，且 ``uv sync`` 依赖它。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import build_meta as _setuptools

_ROOT = Path(__file__).resolve().parent.parent
_WEB_INDEX = _ROOT / "src" / "tianshu" / "web" / "static" / "index.html"
_STAGING = _ROOT / "build"

# 不需要覆盖的 PEP 517 钩子原样转发。
get_requires_for_build_wheel = _setuptools.get_requires_for_build_wheel
get_requires_for_build_sdist = _setuptools.get_requires_for_build_sdist
get_requires_for_build_editable = _setuptools.get_requires_for_build_editable
prepare_metadata_for_build_wheel = _setuptools.prepare_metadata_for_build_wheel
prepare_metadata_for_build_editable = _setuptools.prepare_metadata_for_build_editable
build_sdist = _setuptools.build_sdist
build_editable = _setuptools.build_editable


class WebPayloadMissing(RuntimeError):
    """Web 静态资产缺席时拒绝构建 wheel。"""


def _prune_staging() -> None:
    shutil.rmtree(_STAGING, ignore_errors=True)


def _require_web_payload() -> None:
    if not _WEB_INDEX.is_file():
        raise WebPayloadMissing(
            f"缺少 Web 静态资产：{_WEB_INDEX}\n"
            "wheel 必须自带桌面 Web，否则装上去只有一个空的静态目录。\n"
            "用 scripts/build_release.sh 构建发行物（它会先跑 npm ci && npm run build），"
            "或先在 web/ 下手动构建前端。"
        )


def build_wheel(  # noqa: D103 - PEP 517 钩子
    wheel_directory: str,
    config_settings: dict[str, object] | None = None,
    metadata_directory: str | None = None,
) -> str:
    _prune_staging()
    _require_web_payload()
    return _setuptools.build_wheel(wheel_directory, config_settings, metadata_directory)
