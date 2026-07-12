"""Packaged 默认资源的常驻文件系统视图与可写 overlay 边界。

- packaged 侧：把 catalog 的 Traversable 以 ``importlib.resources.as_file``
  固化为进程级常驻 Path（wheel/zip 安装形态同样可用），供 path-oriented
  消费者（PersonaLoader、TemplateLibrary、SkillsLoader、web mount）读取。
  该视图是只读事实源，任何运行时写/删都不得指向它。
- overlay 侧：可写目标一律位于显式 data 根（如 ``runtime_personas_dir``）。
  COURT.md 的冻结语义：变更只写 override；reset = 删除 override，读取按
  precedence 自然回退 packaged；禁止把 packaged bytes 复制进 overlay。
"""

from __future__ import annotations

from contextlib import ExitStack
from importlib.resources import as_file
from pathlib import Path

from tianshu.resources import catalog


class PackagedDefaults:
    """进程级 packaged 资源目录视图；生命周期覆盖整个应用运行期。"""

    def __init__(self) -> None:
        self._stack = ExitStack()
        self._cache: dict[str, Path] = {}

    def _materialize(self, key: str, node) -> Path:
        if key not in self._cache:
            self._cache[key] = Path(self._stack.enter_context(as_file(node)))
        return self._cache[key]

    def personas_dir(self) -> Path:
        return self._materialize("personas", catalog.persona_defaults())

    def persona_templates_dir(self) -> Path:
        return self._materialize("persona_templates", catalog.persona_templates())

    def builtin_skills_dir(self) -> Path:
        return self._materialize("builtin_skills", catalog.builtin_skills())

    def executor_templates_dir(self) -> Path:
        return self._materialize("executor_templates", catalog.executor_templates())

    def web_static_dir(self) -> Path | None:
        node = catalog.web_static()
        if not node.is_dir():
            return None
        return self._materialize("web_static", node)

    def close(self) -> None:
        self._stack.close()
        self._cache.clear()


_shared: PackagedDefaults | None = None


def packaged_defaults() -> PackagedDefaults:
    global _shared
    if _shared is None:
        _shared = PackagedDefaults()
    return _shared


def reset_packaged_defaults_for_tests() -> None:
    global _shared
    if _shared is not None:
        _shared.close()
    _shared = None


# --- COURT.md overlay（冻结语义） ---


def court_override_path(runtime_personas_dir: Path) -> Path:
    """COURT 变更的唯一合法写目标（data overlay 内）。"""
    return Path(runtime_personas_dir).expanduser() / "court" / "COURT.md"


def resolve_court_read(runtime_personas_dir: Path) -> Path:
    """读取路径：override 存在则用之，否则回退 packaged 默认。"""
    override = court_override_path(runtime_personas_dir)
    if override.is_file():
        return override
    return packaged_defaults().personas_dir() / "court" / "COURT.md"


def reset_court_override(runtime_personas_dir: Path) -> bool:
    """删除 override（幂等）。返回是否存在过。不复制 packaged bytes。"""
    override = court_override_path(runtime_personas_dir)
    if override.is_file():
        override.unlink()
        return True
    return False
