"""lark_cli builtin tool —— 飞书 / Lark 命令行（lark-cli）通用透传。

设计（与用户确认）：
- **通用透传**：把子命令 + flags 作为字符串列表 `args` 传入，调用本机已登录的
  `lark-cli` 二进制，返回 JSON。命令随 CLI 升级自动可用，无需逐命令写死封装。
- **安全**：用 ``create_subprocess_exec``（参数列表，非 shell）避免命令注入；
  读操作自动放行，写动词（send/create/delete…）由 ``LarkCliSafetyRule`` 升级为
  人工审批，auth/config 等交互命令被拒绝。
- **认证**：由人工在 tianshu 主机上 ``lark-cli auth login`` 完成一次（凭证落 keychain），
  本工具只复用会话、不处理登录；检测到未登录时返回友好提示。
"""
from __future__ import annotations

import asyncio
import os
import shutil

from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, error_result

_LARK_BIN_ENV = "TIANSHU_LARK_CLI_BIN"
_DEFAULT_BIN = "lark-cli"
_MAX_OUTPUT = 16000
_DEFAULT_TIMEOUT = 60
_MAX_TIMEOUT = 300

# 交互 / 认证类命令前缀（按非 flag token 比较）：agent 不应触发——
# 会卡在浏览器授权，或改动主机凭证。工具层与策略层双重拦截。
_BLOCKED_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("auth", "login"),
    ("auth", "logout"),
    ("config", "init"),
    ("config", "reset"),
    ("config", "delete"),
)

# 未登录 / 会话失效的特征串（命中即给人工登录提示）
_AUTH_HINTS: tuple[str, ...] = (
    "not logged in", "unauthorized", "login required", "please login",
    "auth required", "未登录", "请登录", "登录已过期", "token expired",
)


def _resolve_bin() -> str | None:
    name = os.environ.get(_LARK_BIN_ENV) or _DEFAULT_BIN
    found = shutil.which(name)
    if found:
        return found
    if os.path.isabs(name) and os.path.exists(name):
        return name
    return None


def _non_flag_head(args: list[str], n: int = 2) -> tuple[str, ...]:
    """取前 n 个非 flag token（去前导 +/-，小写），用于命令前缀比对。"""
    out: list[str] = []
    for a in args:
        if a.startswith("-"):
            continue
        out.append(a.lstrip("+").lower())
        if len(out) >= n:
            break
    return tuple(out)


def _is_blocked(args: list[str]) -> bool:
    head = _non_flag_head(args, 2)
    return any(head[: len(p)] == p for p in _BLOCKED_PREFIXES)


async def lark_cli(args: list[str], timeout: int = _DEFAULT_TIMEOUT) -> ToolResult:
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return error_result(
            'lark_cli: 参数 args 必须是字符串列表，如 ["message", "list"]'
        )
    if not args:
        return error_result("lark_cli: args 不能为空")
    if _is_blocked(args):
        return error_result(
            "lark_cli: 交互/认证类命令（auth login/logout、config init/reset/delete）"
            "需人工在 tianshu 主机上执行，agent 不可触发。"
        )

    bin_path = _resolve_bin()
    if bin_path is None:
        return error_result(
            f"lark_cli: 未找到 lark-cli 二进制。请在主机执行 "
            f"`npx @larksuite/cli@latest install`，或用环境变量 {_LARK_BIN_ENV} 指定路径。"
        )

    # 不自动追加 --format：lark-cli 数据命令默认即 JSON 输出，而部分命令
    # （如 auth status）不接受 --format，盲加会触发 unknown_flag。需要时由
    # 调用方在 args 里自行带 --format。
    cmd = [bin_path, *args]

    try:
        to = max(1, min(int(timeout), _MAX_TIMEOUT))
    except (TypeError, ValueError):
        to = _DEFAULT_TIMEOUT

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,  # 杜绝交互式阻塞
        )
    except (FileNotFoundError, OSError) as exc:
        return error_result(f"lark_cli: 无法执行 {bin_path}：{exc}")

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=to)
    except asyncio.CancelledError:
        proc.kill()
        await asyncio.shield(proc.communicate())
        raise
    except TimeoutError:
        proc.kill()
        await asyncio.shield(proc.communicate())
        return error_result(f"lark_cli: 命令超时（{to}s）")

    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    is_err = proc.returncode != 0

    if is_err:
        low = (out + "\n" + err).lower()
        if any(k in low for k in _AUTH_HINTS):
            tail = f"\n原始错误：{err.strip()[:300]}" if err.strip() else ""
            return error_result(
                "lark_cli: 似乎未登录或会话已失效。请在 tianshu 主机上运行 "
                "`lark-cli auth login` 完成一次授权后重试。" + tail
            )

    content = out + "\nSTDERR:\n" + err if out and err else out or err

    truncated = len(content) > _MAX_OUTPUT
    content = content[:_MAX_OUTPUT]
    return ToolResult(
        content=content,
        details={
            "exit_code": proc.returncode,
            "truncated": truncated,
            "cmd": " ".join(args[:8]),
        },
        is_error=is_err,
    )


def register_lark_cli(registry: ToolRegistry) -> None:
    registry.register(
        "lark_cli",
        lark_cli,
        ToolDefinition(
            name="lark_cli",
            description=(
                "飞书 / Lark 命令行透传工具。把 lark-cli 的子命令与参数按 token 拆成字符串列表 "
                "`args` 传入，调用本机已登录的 lark-cli，返回 JSON。覆盖消息 / 文档 / 表格 / "
                "日历 / 通讯录 / 多维表格 / 任务 / 邮件等域，命令随 CLI 升级自动可用。\n"
                "示例：\n"
                '  args=["message","send","--chat-id","oc_x","--text","hi"]\n'
                '  args=["calendar","+agenda"]\n'
                '  args=["contact","search","--query","张三"]\n'
                "读操作自动执行；写操作（send/create/update/delete 等）会触发人工审批；"
                "auth/config 等交互命令被拒绝（需人工在主机执行）。"
                "数据命令默认输出 JSON；个别命令支持 --format pretty/ndjson，可自行在 args 里带。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "lark-cli 的子命令与参数，按 token 分开，"
                            '如 ["message","list"]。不要拼成单个字符串。'
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数（默认 60，最大 300）",
                    },
                },
                "required": ["args"],
            },
            tier=ToolTier.T2_NETWORK.value,
            max_result_chars=_MAX_OUTPUT,
            side_effect=True,
        ),
    )


__all__ = ["lark_cli", "register_lark_cli"]
