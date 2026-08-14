"""不可变打包资源的稳定定位器。

全部定位基于 ``importlib.resources``，与进程 cwd、HOME、PYTHONPATH 拼写
无关；源码树与安装后的 wheel 使用同一 API。本模块只提供读取定位，
不提供任何写路径——运行时写入属 overlay 层职责。
"""

import hashlib
from importlib import metadata, resources
from importlib.resources.abc import Traversable

_RESOURCE_FAMILIES = (
    "personas",
    "persona_templates",
    "builtin_skills",
    "executor_templates",
    "license",
)


def _resources_root() -> Traversable:
    return resources.files("tianshu.resources")


def persona_defaults() -> Traversable:
    """六个默认 persona 部门与 court 的打包 canonical 树。"""
    return _resources_root() / "personas"


def persona_templates() -> Traversable:
    """persona 模板库（en/zh）与 SOURCES.md。"""
    return _resources_root() / "persona_templates"


def builtin_skills() -> Traversable:
    """内建技能（file-ops、shell）。"""
    return resources.files("tianshu.skills") / "builtin"


def executor_templates() -> Traversable:
    """executor 编排 Markdown 模板包。"""
    return resources.files("tianshu.executor.templates")


def web_static() -> Traversable:
    """桌面 Web 静态资产（vite 构建产物；未构建时目录不存在）。"""
    return resources.files("tianshu") / "web" / "static"


def license_file() -> Traversable:
    """打包 LICENSE 全文（与发行 dist-info 副本同源）。"""
    return _resources_root() / "LICENSE"


def version() -> str:
    """安装制品版本；未安装（纯源码树）时回退到包内 ``__version__``。

    查的是发行名 ``tianshu-agent-os`` 而非 import 名：PyPI 上 ``tianshu`` 是个无关的
    第三方包，若它恰好装在同一环境里，按 import 名查会拿到它的版本号。
    """
    try:
        return metadata.version("tianshu-agent-os")
    except metadata.PackageNotFoundError:
        from tianshu import __version__

        return __version__


def _iter_files(node: Traversable, prefix: str = "") -> list[tuple[str, bytes]]:
    if node.is_file():
        return [(prefix or node.name, node.read_bytes())]
    entries: list[tuple[str, bytes]] = []
    for child in node.iterdir():
        if child.name == "__pycache__":
            continue
        child_prefix = f"{prefix}/{child.name}" if prefix else child.name
        entries.extend(_iter_files(child, child_prefix))
    return entries


def _family_root(family: str) -> Traversable:
    if family == "personas":
        return persona_defaults()
    if family == "persona_templates":
        return persona_templates()
    if family == "builtin_skills":
        return builtin_skills()
    if family == "executor_templates":
        return executor_templates()
    if family == "license":
        return license_file()
    raise ValueError(f"unknown resource family: {family!r}")


def package_digest(families: tuple[str, ...] = _RESOURCE_FAMILIES) -> dict[str, str]:
    """逐 family 的内容聚合摘要（相对路径 + 文件字节，重定位安全）。

    供不可变性断言复用：任何打包资源被运行时改写都会改变对应摘要。
    """
    digests: dict[str, str] = {}
    for family in families:
        aggregate = hashlib.sha256()
        for relative_path, payload in sorted(_iter_files(_family_root(family))):
            aggregate.update(relative_path.encode("utf-8"))
            aggregate.update(b"\x00")
            aggregate.update(hashlib.sha256(payload).digest())
            aggregate.update(b"\x00")
        digests[family] = aggregate.hexdigest()
    return digests
