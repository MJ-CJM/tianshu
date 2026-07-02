"""Markdown file backend — Source of Truth for memory.

Layout (runtime data, NOT in git):
  ~/.tianshu/memory/{persona_id}/MEMORY.md      — core long-term memory (always in context)
  ~/.tianshu/memory/{persona_id}/YYYY-MM-DD.md   — daily append log (searchable)
  ~/.tianshu/memory/court/MEMORY.md              — shared court memory

On first launch, if ~/.tianshu/memory/{persona}/MEMORY.md does not exist,
it is seeded from personas/{persona}/MEMORY.md (the git-tracked template).
"""

from __future__ import annotations

import contextlib
import logging
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class MarkdownMemoryBackend:
    """Read/write memory as Markdown files under ~/.tianshu/memory/."""

    def __init__(self, memory_dir: Path, personas_dir: Path) -> None:
        self._memory_dir = Path(memory_dir).expanduser()
        self._personas_dir = personas_dir

    # ------------------------------------------------------------------
    # Directory & template bootstrapping
    # ------------------------------------------------------------------

    def ensure_dirs(self, persona_ids: list[str] | None = None) -> None:
        """Create memory dirs and seed MEMORY.md from persona templates if missing."""
        if persona_ids is None:
            persona_ids = [
                d.name
                for d in self._personas_dir.iterdir()
                if d.is_dir() and (d / "MEMORY.md").exists()
            ]
        # Always include 'court'
        if "court" not in persona_ids:
            persona_ids.append("court")

        for pid in persona_ids:
            target_dir = self._memory_dir / pid
            target_dir.mkdir(parents=True, exist_ok=True)

            target_memory = target_dir / "MEMORY.md"
            if not target_memory.exists():
                template = self._personas_dir / pid / "MEMORY.md"
                if template.exists():
                    shutil.copy2(template, target_memory)
                    logger.info(
                        "Seeded %s/MEMORY.md from template",
                        pid,
                    )
                else:
                    # Create empty MEMORY.md
                    target_memory.write_text(
                        f"# {pid} Memory\n\n",
                        encoding="utf-8",
                    )

    # ------------------------------------------------------------------
    # Daily log operations
    # ------------------------------------------------------------------

    def append_daily_log(
        self,
        persona_id: str,
        content: str,
        category: str = "W",
        timestamp: datetime | None = None,
    ) -> Path:
        """Append an entry to the daily log file.

        Categories: W(world) / B(biographical) / O(opinion) / S(summary)
        """
        ts = timestamp or datetime.now(UTC)
        date_str = ts.strftime("%Y-%m-%d")
        time_str = ts.strftime("%H:%M")

        persona_dir = self._memory_dir / persona_id
        persona_dir.mkdir(parents=True, exist_ok=True)

        log_path = persona_dir / f"{date_str}.md"
        line = f"- [{time_str}] [{category}] {content}\n"

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)

        return log_path

    # ------------------------------------------------------------------
    # Core MEMORY.md operations
    # ------------------------------------------------------------------

    def read_core_memory(self, persona_id: str) -> str:
        """Read the core MEMORY.md for a persona."""
        path = self._memory_dir / persona_id / "MEMORY.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def write_core_memory(self, persona_id: str, content: str) -> Path:
        """Write/overwrite the core MEMORY.md for a persona."""
        persona_dir = self._memory_dir / persona_id
        persona_dir.mkdir(parents=True, exist_ok=True)
        path = persona_dir / "MEMORY.md"
        path.write_text(content, encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # Section-anchored writes (memory_write tool 后端)
    # ------------------------------------------------------------------

    def list_sections_by_size(self, persona_id: str) -> list[tuple[str, int]]:
        """返回 [(section_header, body_chars), ...]，按 body_chars 倒序，便于 trim 提示。

        section_header 形如 '## xxx'；body_chars 不含 header 行本身。
        文件不存在或无 H2 段时返回空列表。
        """
        path = self._memory_dir / persona_id / "MEMORY.md"
        if not path.exists():
            return []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        lines = text.splitlines(keepends=True)
        sections: list[tuple[str, int]] = []
        cur_header: str | None = None
        cur_chars = 0
        for line in lines:
            if line.startswith("## "):
                if cur_header is not None:
                    sections.append((cur_header, cur_chars))
                cur_header = line.rstrip("\n")
                cur_chars = 0
            elif cur_header is not None:
                cur_chars += len(line)
        if cur_header is not None:
            sections.append((cur_header, cur_chars))
        sections.sort(key=lambda x: x[1], reverse=True)
        return sections

    def write_section(
        self,
        persona_id: str,
        section: str,
        *,
        mode: str = "append",
        content: str | None = None,
        old_text: str | None = None,
    ) -> tuple[Path, int]:
        """以 H2 section 为锚定的安全写入。

        参数
        ----
        persona_id: 写到 ~/.tianshu/memory/{persona_id}/MEMORY.md
        section:    完整的 H2 锚（如 "## 心学要旨"）；调用方应已通过
                    safety.normalize_section 归一化
        mode:       "append" | "replace" | "remove" | "set"
        content:    append/replace/set 必填
        old_text:   replace/remove 必填

        返回
        ----
        (写入后的文件路径, 写入后的总字符数)

        副作用
        ----
        - 写前 cp 一份到 MEMORY.md.bak（覆盖式，仅保留最近一份）
        - 原子写：写到 .tmp 然后 os.replace
        - macOS/Linux 上加 fcntl.flock 排他锁

        异常
        ----
        - FileNotFoundError: replace/remove 时 section 或 old_text 不存在
        - ValueError: 参数缺失（如 append/replace/set 缺 content）
        - 调用方应在调用前用 safety.validate_content / check_file_size 做内容校验
        """
        if mode not in ("append", "replace", "remove", "set"):
            raise ValueError(f"unsupported write_section mode: {mode}")
        if mode in ("append", "replace", "set") and not content:
            raise ValueError(f"mode={mode} requires non-empty content")
        if mode in ("replace", "remove") and not old_text:
            raise ValueError(f"mode={mode} requires old_text")
        if not section.startswith("## "):
            raise ValueError("section must be normalized to '## xxx' form")

        persona_dir = self._memory_dir / persona_id
        persona_dir.mkdir(parents=True, exist_ok=True)
        path = persona_dir / "MEMORY.md"

        # 锁 + 读现有内容 + 计算新内容 + 原子写
        import fcntl
        import os

        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        new_text = self._mutate_section(
            existing,
            section,
            mode=mode,
            content=content,
            old_text=old_text,
        )

        # 整文件上限：前置检查并附 trim 建议
        from tianshu.memory.safety import MAX_FILE_CHARS, MemorySafetyError

        if len(new_text) > MAX_FILE_CHARS:
            top = self.list_sections_by_size(persona_id)[:3]
            if top:
                hint_lines = ["最大的 3 个 section（建议先 remove 旧条目腾位）："]
                for h, c in top:
                    hint_lines.append(f"  - {h}：{c} 字")
                hint_lines.append(
                    f'示例：memory_write(action="remove", scope=…, section="{top[0][0]}", old_text="…要删的旧文本…")',
                )
                hint = "\n".join(hint_lines)
            else:
                hint = "（暂无可清理 section，请用 replace 缩短现有 content）"
            raise MemorySafetyError(
                f"MEMORY.md 写入后将达 {len(new_text)} 字，超出 {MAX_FILE_CHARS} 字上限。\n{hint}",
            )

        # 备份（仅当文件已存在时）
        if path.exists():
            try:
                shutil.copy2(path, path.with_suffix(".md.bak"))
            except OSError:
                logger.warning("backup MEMORY.md.bak failed for %s", persona_id)

        tmp_path = path.with_suffix(".md.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            with contextlib.suppress(OSError, AttributeError):  # Windows 或不支持 flock 的 fs
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(new_text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)

        return path, len(new_text)

    @staticmethod
    def _mutate_section(
        existing: str,
        section: str,
        *,
        mode: str,
        content: str | None,
        old_text: str | None,
    ) -> str:
        """纯函数：在文本中按 H2 section 锚点执行 append/replace/remove。

        section 不存在时：append 创建新段；replace/remove 抛 FileNotFoundError。
        """
        # 切片：找到 section 的起止行
        lines = existing.splitlines(keepends=True)
        start = -1
        end = len(lines)
        for i, line in enumerate(lines):
            if start < 0 and line.rstrip("\n") == section:
                start = i
                continue
            if start >= 0 and line.startswith("## "):
                end = i
                break

        if start < 0:
            # section 不存在
            if mode in ("replace", "remove"):
                raise FileNotFoundError(f"section {section!r} not found in MEMORY.md")
            # append / set：创建新段，追加到文件末尾
            base = existing.rstrip()
            sep = "\n\n" if base else ""
            return f"{base}{sep}{section}\n\n{content.rstrip()}\n"

        section_body = "".join(lines[start + 1 : end])
        before = "".join(lines[:start])
        # 保留 section header 与 body
        if mode == "append":
            # 去重：内容若完全包含在已有 body 中则拒绝
            if content.strip() in section_body:
                raise ValueError("content already present in this section (dedupe)")
            new_body = section_body.rstrip() + "\n\n" + content.rstrip() + "\n"
            new_section = f"{section}\n{new_body}"
        elif mode == "set":
            # 整段 body 覆盖：只动本 section，其余 section 原样保留
            new_section = f"{section}\n\n{content.rstrip()}\n"
        elif mode == "replace":
            if old_text not in section_body:
                raise FileNotFoundError(f"old_text not found in section {section!r}")
            new_body = section_body.replace(old_text, content, 1)
            new_section = f"{section}\n{new_body}"
        else:  # remove
            if old_text not in section_body:
                raise FileNotFoundError(f"old_text not found in section {section!r}")
            new_body = section_body.replace(old_text, "", 1)
            # 若移除后 section body 全空，则把整个 section 也移掉
            new_section = "" if not new_body.strip() else f"{section}\n{new_body}"

        after = "".join(lines[end:])
        result = before + new_section + after
        # 规整连续空行
        while "\n\n\n\n" in result:
            result = result.replace("\n\n\n\n", "\n\n\n")
        return result

    # ------------------------------------------------------------------
    # Search & retrieval
    # ------------------------------------------------------------------

    def search_daily_logs(
        self,
        persona_id: str,
        query: str,
        limit: int = 20,
    ) -> list[dict]:
        """Search daily log files for matching entries."""
        persona_dir = self._memory_dir / persona_id
        if not persona_dir.exists():
            return []

        results: list[dict] = []
        query_lower = query.lower()

        # Search most recent files first (YYYY-MM-DD.md pattern)
        log_files = sorted(
            (f for f in persona_dir.glob("????-??-??.md")),
            reverse=True,
        )
        for log_file in log_files[:30]:  # Scan last 30 days max
            try:
                text = log_file.read_text(encoding="utf-8")
            except Exception:
                continue

            for line in text.splitlines():
                if query_lower in line.lower():
                    results.append(
                        {
                            "file": log_file.name,
                            "date": log_file.stem,
                            "content": line.strip(),
                        }
                    )
                    if len(results) >= limit:
                        return results

        return results

    def list_daily_entries(
        self,
        persona_id: str,
        days: int = 7,
    ) -> list[str]:
        """List recent daily log entries."""
        persona_dir = self._memory_dir / persona_id
        if not persona_dir.exists():
            return []

        entries: list[str] = []
        log_files = sorted(
            (f for f in persona_dir.glob("????-??-??.md")),
            reverse=True,
        )
        for log_file in log_files[:days]:
            try:
                text = log_file.read_text(encoding="utf-8")
                entries.extend(
                    line.strip() for line in text.splitlines() if line.strip().startswith("- ")
                )
            except Exception:
                continue

        return entries

    def read_recent_logs(
        self,
        persona_id: str,
        days: int = 2,
        char_budget: int = 2000,
    ) -> str:
        """Read recent N days of logs, trimmed to char_budget.

        Returns formatted text suitable for prompt injection.
        Keeps most recent entries when over budget.
        """
        persona_dir = self._memory_dir / persona_id
        if not persona_dir.exists():
            return ""

        today = datetime.now(UTC).date()
        entries: list[str] = []

        for offset in range(days):
            day = today - timedelta(days=offset)
            log_path = persona_dir / f"{day.isoformat()}.md"
            if not log_path.exists():
                continue
            try:
                text = log_path.read_text(encoding="utf-8")
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("- "):
                        entries.append(stripped)
            except Exception:
                continue

        if not entries:
            return ""

        # Keep most recent entries first (they are already newest-first by file order)
        # Trim to budget
        result_lines: list[str] = []
        remaining = char_budget
        for entry in entries:
            if len(entry) + 1 > remaining:
                break
            result_lines.append(entry)
            remaining -= len(entry) + 1  # +1 for newline

        if not result_lines:
            return ""

        return "\n".join(result_lines)

    # ------------------------------------------------------------------
    # Delete operations
    # ------------------------------------------------------------------

    def delete_line(
        self, persona_id: str, content: str, created_at: datetime | None = None
    ) -> bool:
        """Delete a matching line from the daily log file.

        Matches by content substring. If created_at is provided, narrows to
        the specific date file and time prefix.
        Returns True if a line was removed.
        """
        persona_dir = self._memory_dir / persona_id
        if not persona_dir.exists():
            return False

        # Determine which file(s) to search
        if created_at:
            date_str = created_at.strftime("%Y-%m-%d")
            candidates = [persona_dir / f"{date_str}.md"]
        else:
            candidates = sorted(persona_dir.glob("????-??-??.md"), reverse=True)

        for log_file in candidates:
            if not log_file.exists():
                continue
            try:
                text = log_file.read_text(encoding="utf-8")
            except Exception:
                continue

            lines = text.splitlines(keepends=True)
            # Find the first line matching the content
            found_idx = -1
            for i, line in enumerate(lines):
                if content in line:
                    found_idx = i
                    break

            if found_idx < 0:
                continue

            # Remove the matched line and any continuation lines
            # (continuation = lines not starting with "- [")
            end_idx = found_idx + 1
            while end_idx < len(lines):
                stripped = lines[end_idx].strip()
                if stripped.startswith("- [") or stripped == "":
                    break
                end_idx += 1
            # Also skip trailing blank line
            if end_idx < len(lines) and lines[end_idx].strip() == "":
                end_idx += 1

            new_lines = lines[:found_idx] + lines[end_idx:]
            new_text = "".join(new_lines)

            if new_text.strip():
                log_file.write_text(new_text, encoding="utf-8")
            else:
                log_file.unlink()  # Delete empty file
            logger.info("Deleted MD line for '%s' from %s", content[:40], log_file.name)
            return True

        return False

    # ------------------------------------------------------------------
    # SQLite index sync (derived index rebuild)
    # ------------------------------------------------------------------

    def sync_to_sqlite(self, storage: Storage, persona_id: str) -> int:
        """Scan MD files and rebuild SQLite memory_entries index for a persona.

        Returns the number of entries synced.
        """
        from tianshu.memory.models import MemoryEntry

        persona_dir = self._memory_dir / persona_id
        if not persona_dir.exists():
            return 0

        # Parse category from log line: - [HH:MM] [X] content
        _LOG_RE = re.compile(
            r"^- \[(\d{2}:\d{2})\] \[([WBOS])\] (.+)$",
        )
        category_map = {"W": "observation", "B": "entity", "O": "insight", "S": "summary"}

        synced = 0

        # Process daily log files
        log_files = sorted(persona_dir.glob("????-??-??.md"))
        for log_file in log_files:
            date_str = log_file.stem
            try:
                text = log_file.read_text(encoding="utf-8")
            except Exception:
                continue

            for line in text.splitlines():
                m = _LOG_RE.match(line.strip())
                if not m:
                    continue
                time_str, cat_code, content = m.groups()
                try:
                    ts = datetime.strptime(
                        f"{date_str} {time_str}",
                        "%Y-%m-%d %H:%M",
                    ).replace(tzinfo=UTC)
                except ValueError:
                    ts = datetime.now(UTC)

                entry = MemoryEntry(
                    persona_id=persona_id,
                    category=category_map.get(cat_code, "observation"),
                    content=content,
                    source="agent",
                    created_at=ts,
                )
                storage.save_memory_entry(entry)
                synced += 1

        # Process core MEMORY.md insights
        core_path = persona_dir / "MEMORY.md"
        if core_path.exists():
            try:
                text = core_path.read_text(encoding="utf-8")
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("- ") and len(stripped) > 4:
                        entry = MemoryEntry(
                            persona_id=persona_id,
                            category="insight",
                            content=stripped[2:],
                            source="reflection",
                        )
                        storage.save_memory_entry(entry)
                        synced += 1
            except Exception:
                pass

        logger.info("Synced %d entries from MD to SQLite for %s", synced, persona_id)
        return synced
