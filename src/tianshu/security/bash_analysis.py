"""bash 命令的 quote-aware 分段与风险分级(迭代 3「深防御」)。

移植 zeroclaw ``security/policy.rs`` 的核心思路:朴素子串匹配可被
`git log; rm -rf /` 绕过——命令以 `git ` 开头,旧白名单 startswith 直接放行。
正确做法是**按未加引号的分隔符切段,逐段判定,最高危胜出**:

- 分段:`;` `|` `||` `&&` `换行` 都切段;引号内的分隔符不算。
- 白名单:必须**每一段**都命中前缀才放行(全体达标才安全)。
- 黑名单:**任一段**命中即 deny。
- 结构绕过:未加引号的重定向 `>`/`>>`、命令替换 `$(...)`/反引号、后台 `&`
  会引入白名单看不见的隐藏子命令,直接升级审批(不放行)。
"""

from __future__ import annotations

from dataclasses import dataclass


def split_unquoted_segments(command: str) -> list[str]:
    """按未加引号的 shell 分隔符(; | || && 换行)切段;引号内分隔符保留。

    单引号内完全字面;双引号内处理转义。返回去空后的非空段列表。
    """
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None  # None / "'" / '"'
    escaped = False
    i = 0
    n = len(command)

    def flush() -> None:
        seg = "".join(current).strip()
        if seg:
            segments.append(seg)
        current.clear()

    while i < n:
        ch = command[i]
        if quote == "'":
            current.append(ch)
            if ch == "'":
                quote = None
        elif quote == '"':
            if escaped:
                escaped = False
                current.append(ch)
            elif ch == "\\":
                escaped = True
                current.append(ch)
            elif ch == '"':
                quote = None
                current.append(ch)
            else:
                current.append(ch)
        else:  # 无引号
            if escaped:
                escaped = False
                current.append(ch)
            elif ch == "\\":
                escaped = True
                current.append(ch)
            elif ch == "'":
                quote = "'"
                current.append(ch)
            elif ch == '"':
                quote = '"'
                current.append(ch)
            elif ch in (";", "\n"):
                flush()
            elif ch == "|":
                if i + 1 < n and command[i + 1] == "|":
                    i += 1  # 吃掉 ||
                flush()
            elif ch == "&":
                if i + 1 < n and command[i + 1] == "&":
                    i += 1  # && 是分隔符
                    flush()
                else:
                    current.append(ch)  # 单 & 由结构检测处理
            else:
                current.append(ch)
        i += 1

    flush()
    return segments


def _contains_unquoted(command: str, targets: set[str]) -> bool:
    """command 中是否存在未加引号的 targets 字符之一。"""
    quote: str | None = None
    escaped = False
    for ch in command:
        if quote == "'":
            if ch == "'":
                quote = None
        elif quote == '"':
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quote = None
        else:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "'":
                quote = "'"
            elif ch == '"':
                quote = '"'
            elif ch in targets:
                return True
    return False


def has_unquoted_single_ampersand(command: str) -> bool:
    """检测独立的未加引号 `&`(后台执行/隐藏链);`&&` 不算。"""
    quote: str | None = None
    escaped = False
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if quote == "'":
            if ch == "'":
                quote = None
        elif quote == '"':
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quote = None
        else:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "'":
                quote = "'"
            elif ch == '"':
                quote = '"'
            elif ch == "&":
                if i + 1 < n and command[i + 1] == "&":
                    i += 1  # 跳过 &&
                else:
                    return True
        i += 1
    return False


def has_unquoted_command_substitution(command: str) -> bool:
    """检测未加引号的命令替换:`$(...)` 或反引号(隐藏子命令的常见通道)。"""
    if _contains_unquoted(command, {"`"}):
        return True
    quote: str | None = None
    escaped = False
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if quote == "'":
            if ch == "'":
                quote = None
        elif quote == '"':
            # 双引号内 $(...) 仍会展开,视为不安全
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quote = None
            elif ch == "$" and i + 1 < n and command[i + 1] == "(":
                return True
        else:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "'":
                quote = "'"
            elif ch == '"':
                quote = '"'
            elif ch == "$" and i + 1 < n and command[i + 1] == "(":
                return True
        i += 1
    return False


def has_unquoted_redirection(command: str) -> bool:
    """检测未加引号的重定向 `>` / `>>` / `<`(可覆写文件/绕过工作区读写)。"""
    return _contains_unquoted(command, {">", "<"})


@dataclass(frozen=True)
class BashAnalysis:
    segments: tuple[str, ...]
    has_background: bool  # 单 &
    has_substitution: bool  # $(...) / ``
    has_redirection: bool  # > < >>

    @property
    def has_structural_risk(self) -> bool:
        return self.has_background or self.has_substitution or self.has_redirection

    @property
    def structural_notes(self) -> list[str]:
        notes = []
        if self.has_background:
            notes.append("background '&'")
        if self.has_substitution:
            notes.append("command substitution")
        if self.has_redirection:
            notes.append("redirection")
        return notes


def analyze_command(command: str) -> BashAnalysis:
    return BashAnalysis(
        segments=tuple(split_unquoted_segments(command)),
        has_background=has_unquoted_single_ampersand(command),
        has_substitution=has_unquoted_command_substitution(command),
        has_redirection=has_unquoted_redirection(command),
    )
