"""技能安全安装管线 —— 把外部技能源(目录或 .zip)安全落地到用户技能目录。

多层防护(借鉴 deer-flow installer):
1. 防路径穿越:成员名不得含 ``..``、不得为绝对路径,解压后真实路径必须落在暂存目录内。
2. 防 symlink:拒绝任何 symlink 成员(zip 的 symlink 位 / 目录的 ``is_symlink()``)。
3. 防 zip 炸弹:预检累加解压后字节数、成员数、单文件大小,超限即拒绝(不先解压再判断)。
4. 结构校验 + 安全扫描:``SkillValidator.validate`` + ``SkillsGuard.scan_content``,
   并按信任级经 ``should_allow`` 决定放行。

任一关不过即拒绝,不落地任何文件(失败安全)。公开安装入口即使校验通过也只返回稳定拒绝；
技能写入统一由受治理的 ``SkillInstallService`` 创建候选并进入门控流程。
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

import frontmatter as fm

from tianshu.skills.guard import SkillsGuard, TrustLevel
from tianshu.skills.validator import SkillValidator, ValidationFinding

_DEFAULT_MAX_TOTAL_BYTES = 10 * 1024 * 1024  # 解压后总字节上限:10 MiB
_DEFAULT_MAX_MEMBERS = 512  # 成员数上限
_DEFAULT_MAX_FILE_BYTES = 4 * 1024 * 1024  # 单文件上限:4 MiB
_SKILL_RESOURCE_DIRS = ("scripts", "references", "assets", "templates")


@dataclass(frozen=True)
class InstallResult:
    """安装结果。失败时 ``installed=False`` 且 ``reason`` 说明卡在哪一关。"""

    installed: bool
    skill_name: str | None
    reason: str
    findings: tuple[ValidationFinding, ...] = ()


@dataclass(frozen=True)
class SkillPackageMember:
    path: str
    kind: Literal["file", "directory", "symlink_file", "symlink_directory"]
    content: str | None = None


class SkillPackageSnapshotError(ValueError):
    """The loader-selected live package cannot be safely snapshotted."""


@dataclass(frozen=True)
class PackageValidationResult:
    valid: bool
    skill_name: str | None
    reason: str
    findings: tuple[ValidationFinding, ...] = ()


class _Reject(Exception):
    """内部信号:某一关不通过,携带原因与结构化 findings。"""

    def __init__(self, reason: str, findings: tuple[ValidationFinding, ...] = ()) -> None:
        super().__init__(reason)
        self.reason = reason
        self.findings = findings


def canonical_skill_package_member_path(value: str) -> str:
    """Return one safe POSIX target for an in-memory package member."""

    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or re.match(r"^[A-Za-z]:", value) is not None
        or any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)
        or ".." in value.split("/")
    ):
        raise ValueError("skill package member path is invalid")
    canonical = path.as_posix()
    if canonical in {"", "."}:
        raise ValueError("skill package member cannot target package root")
    return canonical


def snapshot_skill_package(
    skill_file: Path,
    *,
    expected_name: str,
    max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
    max_members: int = _DEFAULT_MAX_MEMBERS,
    max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES,
) -> tuple[SkillPackageMember, ...]:
    """Read one loader-selected live skill and its allowed resource trees safely."""

    source = Path(skill_file)
    root = source.parent
    if source.name != "SKILL.md" or root.name != expected_name:
        raise SkillPackageSnapshotError("loader skill identity does not match requested skill")
    if root.is_symlink():
        raise SkillPackageSnapshotError("skill root cannot be a symlink")
    try:
        if not root.is_dir():
            raise SkillPackageSnapshotError("skill root is not a directory")
        resolved_root = root.resolve(strict=True)
        resolved_source = source.resolve(strict=True)
    except OSError as exc:
        raise SkillPackageSnapshotError("loader skill path cannot be resolved") from exc
    if resolved_source.parent != resolved_root:
        raise SkillPackageSnapshotError("loader skill path escapes its package root")

    members: list[SkillPackageMember] = []
    total_bytes = 0

    def append(member: SkillPackageMember, *, byte_count: int = 0) -> None:
        nonlocal total_bytes
        if len(members) >= max_members:
            raise SkillPackageSnapshotError("skill package member count exceeds limit")
        total_bytes += byte_count
        if total_bytes > max_total_bytes:
            raise SkillPackageSnapshotError("skill package total size exceeds limit")
        members.append(member)

    skill_content, skill_size = _read_snapshot_file(source, max_file_bytes=max_file_bytes)
    append(
        SkillPackageMember(path="SKILL.md", kind="file", content=skill_content),
        byte_count=skill_size,
    )

    def visit_directory(directory: Path, relative: str) -> None:
        try:
            before = os.lstat(directory)
        except OSError as exc:
            raise SkillPackageSnapshotError("skill resource directory cannot be read") from exc
        if stat.S_ISLNK(before.st_mode):
            raise SkillPackageSnapshotError("skill package contains a symlink directory")
        if not stat.S_ISDIR(before.st_mode):
            raise SkillPackageSnapshotError("skill resource root is not a directory")
        try:
            canonical = canonical_skill_package_member_path(relative)
        except ValueError as exc:
            raise SkillPackageSnapshotError("skill package contains an illegal path") from exc
        if canonical != relative:
            raise SkillPackageSnapshotError("skill package contains an illegal path")
        append(SkillPackageMember(path=relative, kind="directory"))
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            raise SkillPackageSnapshotError("skill resource directory cannot be read") from exc
        for entry in entries:
            child_relative = f"{relative}/{entry.name}"
            try:
                canonical_child = canonical_skill_package_member_path(child_relative)
                child_stat = entry.stat(follow_symlinks=False)
            except ValueError as exc:
                raise SkillPackageSnapshotError("skill package contains an illegal path") from exc
            except OSError as exc:
                raise SkillPackageSnapshotError("skill resource member cannot be read") from exc
            if canonical_child != child_relative:
                raise SkillPackageSnapshotError("skill package contains an illegal path")
            child = Path(entry.path)
            if stat.S_ISLNK(child_stat.st_mode):
                raise SkillPackageSnapshotError("skill package contains a symlink member")
            if stat.S_ISDIR(child_stat.st_mode):
                visit_directory(child, child_relative)
                continue
            if not stat.S_ISREG(child_stat.st_mode):
                raise SkillPackageSnapshotError("skill package contains a non-ordinary member")
            content, size = _read_snapshot_file(
                child,
                max_file_bytes=max_file_bytes,
                expected_stat=child_stat,
            )
            append(
                SkillPackageMember(path=child_relative, kind="file", content=content),
                byte_count=size,
            )
        try:
            after = os.lstat(directory)
        except OSError as exc:
            raise SkillPackageSnapshotError(
                "skill resource directory changed during snapshot"
            ) from exc
        if (before.st_dev, before.st_ino, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
        ):
            raise SkillPackageSnapshotError("skill resource directory changed during snapshot")

    for resource_name in _SKILL_RESOURCE_DIRS:
        resource = root / resource_name
        if os.path.lexists(resource):
            visit_directory(resource, resource_name)
    return tuple(sorted(members, key=lambda member: member.path))


def _read_snapshot_file(
    path: Path,
    *,
    max_file_bytes: int,
    expected_stat: os.stat_result | None = None,
) -> tuple[str, int]:
    """Read a stable ordinary UTF-8 file descriptor without following symlinks."""

    try:
        before = expected_stat or os.lstat(path)
    except OSError as exc:
        raise SkillPackageSnapshotError("skill package file cannot be read") from exc
    if stat.S_ISLNK(before.st_mode):
        raise SkillPackageSnapshotError("skill package contains a symlink file")
    if not stat.S_ISREG(before.st_mode):
        raise SkillPackageSnapshotError("skill package contains a non-ordinary file")
    if before.st_size > max_file_bytes:
        raise SkillPackageSnapshotError("skill package file size exceeds limit")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise SkillPackageSnapshotError("safe no-follow file reads are unavailable")
    flags = os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SkillPackageSnapshotError("skill package file cannot be safely opened") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (
            opened.st_dev,
            opened.st_ino,
        ):
            raise SkillPackageSnapshotError("skill package file changed during snapshot")
        chunks: list[bytes] = []
        byte_count = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, max_file_bytes - byte_count + 1))
            if not chunk:
                break
            chunks.append(chunk)
            byte_count += len(chunk)
            if byte_count > max_file_bytes:
                raise SkillPackageSnapshotError("skill package file size exceeds limit")
        finished = os.fstat(descriptor)
        if (opened.st_size, opened.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns):
            raise SkillPackageSnapshotError("skill package file changed during snapshot")
    finally:
        os.close(descriptor)
    try:
        after = os.lstat(path)
    except OSError as exc:
        raise SkillPackageSnapshotError("skill package file changed during snapshot") from exc
    if (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino):
        raise SkillPackageSnapshotError("skill package file changed during snapshot")
    try:
        return b"".join(chunks).decode("utf-8"), byte_count
    except UnicodeDecodeError as exc:
        raise SkillPackageSnapshotError("skill package file is not UTF-8") from exc


class SkillInstaller:
    """把外部技能源安全安装进用户技能根目录。"""

    def __init__(
        self,
        target_root: Path,
        *,
        validator: SkillValidator | None = None,
        guard: SkillsGuard | None = None,
        max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
        max_members: int = _DEFAULT_MAX_MEMBERS,
        max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        self._target_root = Path(target_root)
        self._validator = validator or SkillValidator()
        self._guard = guard or SkillsGuard()
        self._max_total_bytes = max_total_bytes
        self._max_members = max_members
        self._max_file_bytes = max_file_bytes

    def install(
        self,
        source: Path,
        *,
        source_trust: str = "community",
        trust_level: TrustLevel | None = None,
    ) -> InstallResult:
        """Refuse the retired live-write entry without touching source or target."""
        del source, source_trust, trust_level
        return InstallResult(False, None, "governed_skill_service_required")

    def validate_package(
        self,
        members: tuple[SkillPackageMember, ...],
        *,
        declared_name: str,
        source_trust: str = "community",
        trust_level: TrustLevel | None = None,
    ) -> PackageValidationResult:
        """Validate a complete in-memory package without installing it."""

        level = trust_level or SkillsGuard.resolve_trust_level(source_trust)
        with tempfile.TemporaryDirectory(prefix="tianshu-skill-package-") as temporary:
            staging = Path(temporary)
            try:
                self._stage_members(members, staging)
                _skill_root, name, findings = self._validate_staged(
                    staging, source_trust=source_trust, trust_level=level
                )
                if name != declared_name:
                    raise _Reject("技能名与包声明不一致", findings)
                return PackageValidationResult(True, name, "校验通过", findings)
            except _Reject as rejected:
                return PackageValidationResult(False, None, rejected.reason, rejected.findings)

    def _stage_members(
        self, members: tuple[SkillPackageMember, ...], staging: Path
    ) -> tuple[SkillPackageMember, ...]:
        if len(members) > self._max_members:
            raise _Reject(f"成员数超限: {len(members)} > {self._max_members}")
        paths: set[str] = set()
        total = 0
        canonical_members: list[SkillPackageMember] = []
        for member in members:
            try:
                canonical_path = canonical_skill_package_member_path(member.path)
            except ValueError as exc:
                raise _Reject("技能包成员路径不规范") from exc
            if canonical_path in paths:
                raise _Reject("技能包含重复成员路径")
            paths.add(canonical_path)
            if canonical_path != member.path:
                raise _Reject("技能包成员路径不规范")
            canonical_members.append(
                SkillPackageMember(
                    path=canonical_path,
                    kind=member.kind,
                    content=member.content,
                )
            )
        normalized = tuple(sorted(canonical_members, key=lambda member: member.path))
        for member in normalized:
            target = self._safe_target(staging, member.path)
            if member.kind in {"symlink_file", "symlink_directory"}:
                raise _Reject("拒绝 symlink 成员")
            if member.kind == "directory":
                if member.content is not None:
                    raise _Reject("目录成员不能包含内容")
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.content is None:
                raise _Reject("文件成员缺少内容")
            data = member.content.encode("utf-8")
            if len(data) > self._max_file_bytes:
                raise _Reject("技能包单文件超限")
            total += len(data)
            if total > self._max_total_bytes:
                raise _Reject("技能包总大小超限")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with target.open("x", encoding="utf-8") as stream:
                    stream.write(member.content)
            except (FileExistsError, IsADirectoryError, NotADirectoryError) as exc:
                raise _Reject("技能包成员路径冲突") from exc
        return normalized

    def _validate_staged(
        self, staging: Path, *, source_trust: str, trust_level: TrustLevel
    ) -> tuple[Path, str, tuple[ValidationFinding, ...]]:
        skill_root = self._locate_skill_root(staging)
        content = self._read_skill_md(skill_root)
        name = self._frontmatter_name(content)
        validation = self._validator.validate(name or "", content, source_trust)
        if not validation.valid:
            raise _Reject("结构校验未通过", validation.findings)
        guard_result = self._guard.scan_content(content, trust_level)
        if not SkillsGuard.should_allow(guard_result, trust_level):
            raise _Reject(
                f"安全策略拒绝(verdict={guard_result.verdict}, trust={trust_level.value})",
                validation.findings,
            )
        if not name:
            raise _Reject("缺少技能名", validation.findings)
        return skill_root, name, validation.findings

    # ---------- 暂存(zip) ----------

    def _stage_zip(self, src: Path, staging: Path) -> None:
        try:
            with zipfile.ZipFile(src) as zf:
                infos = zf.infolist()
                self._check_zip_limits(infos)
                for info in infos:
                    self._guard_zip_member(info, staging)
                zf.extractall(staging)  # 预检全过后才解压
        except zipfile.BadZipFile as e:
            raise _Reject(f"无效的 zip: {e}") from e

    def _check_zip_limits(self, infos: list[zipfile.ZipInfo]) -> None:
        files = [i for i in infos if not i.is_dir()]
        if len(files) > self._max_members:
            raise _Reject(f"成员数超限: {len(files)} > {self._max_members}")
        total = 0
        for info in files:
            if info.file_size > self._max_file_bytes:
                raise _Reject(f"单文件超限: {info.filename} ({info.file_size}B)")
            total += info.file_size
            if total > self._max_total_bytes:
                raise _Reject(f"解压总大小超限: > {self._max_total_bytes}B")

    def _guard_zip_member(self, info: zipfile.ZipInfo, staging: Path) -> None:
        if stat.S_ISLNK(info.external_attr >> 16):
            raise _Reject(f"拒绝 symlink 成员: {info.filename}")
        self._safe_target(staging, info.filename)

    # ---------- 暂存(目录) ----------

    def _stage_dir(self, src: Path, staging: Path) -> None:
        total = 0
        count = 0
        for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
            for d in dirnames:
                if os.path.islink(os.path.join(dirpath, d)):
                    raise _Reject(f"拒绝 symlink 目录: {d}")
            for f in filenames:
                fp = Path(dirpath) / f
                if fp.is_symlink():
                    raise _Reject(f"拒绝 symlink 文件: {fp.name}")
                size = fp.stat().st_size
                if size > self._max_file_bytes:
                    raise _Reject(f"单文件超限: {fp.name} ({size}B)")
                total += size
                count += 1
                if count > self._max_members:
                    raise _Reject(f"成员数超限: > {self._max_members}")
                if total > self._max_total_bytes:
                    raise _Reject(f"解压总大小超限: > {self._max_total_bytes}B")
        shutil.copytree(src, staging / "content")

    # ---------- 定位与解析 ----------

    @staticmethod
    def _safe_target(root: Path, name: str) -> Path:
        """校验成员名不越界并返回解析后的目标路径。"""
        if name.startswith("/") or "\\" in name or PurePosixPath(name).is_absolute():
            raise _Reject(f"非法成员路径(绝对): {name}")
        if ".." in PurePosixPath(name).parts:
            raise _Reject(f"路径穿越: {name}")
        target = (root / name).resolve()
        if target != root.resolve() and not target.is_relative_to(root.resolve()):
            raise _Reject(f"路径逃逸: {name}")
        return target

    @staticmethod
    def _locate_skill_root(staging: Path) -> Path:
        """定位含 SKILL.md 的技能根:暂存根,或唯一一层包裹目录。"""
        skill_files = [path for path in staging.rglob("SKILL.md") if path.is_file()]
        if not skill_files:
            raise _Reject("未找到 SKILL.md")
        if len(skill_files) != 1:
            raise _Reject("SKILL.md 数量必须为 1")
        if skill_files[0] == staging / "SKILL.md":
            return staging
        children = [c for c in staging.iterdir() if c.is_dir()]
        if len(children) == 1 and skill_files[0] == children[0] / "SKILL.md":
            return children[0]
        raise _Reject("SKILL.md 必须位于包根或唯一一层包装目录")

    @staticmethod
    def _read_skill_md(skill_root: Path) -> str:
        try:
            return (skill_root / "SKILL.md").read_text("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise _Reject(f"读取 SKILL.md 失败: {e}") from e

    @staticmethod
    def _frontmatter_name(content: str) -> str | None:
        try:
            post = fm.loads(content)
        except Exception:
            return None
        name = (post.metadata or {}).get("name")
        return name if isinstance(name, str) else None
