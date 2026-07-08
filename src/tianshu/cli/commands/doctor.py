"""tianshu doctor — 装机自检(配置/DB/端口/可选依赖,可选 LLM 连通)。

离线检查默认全跑(不花钱、秒级);`--llm` 才做一次真实最小调用。
输出结构化诊断(ok/warn/fail + 修复建议),存在 fail 时以退出码 1 结束。
"""

from __future__ import annotations

import importlib.util
import socket
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

console = Console()

_OK = "[green]✓ ok[/green]"
_WARN = "[yellow]⚠ warn[/yellow]"
_FAIL = "[red]✗ fail[/red]"


@dataclass(frozen=True)
class DiagResult:
    name: str
    level: str  # ok | warn | fail
    detail: str
    hint: str = ""


def _check_api_key(settings) -> DiagResult:
    if not settings.llm_api_key:
        return DiagResult(
            "LLM API Key",
            "fail",
            "TIANSHU_LLM_API_KEY 为空",
            "复制 .env.example 为 .env 并填入密钥",
        )
    masked = settings.llm_api_key[:6] + "…" if len(settings.llm_api_key) > 6 else "已设置"
    return DiagResult("LLM API Key", "ok", f"{masked}(model={settings.llm_model})")


def _check_db(settings) -> DiagResult:
    db_path = Path(settings.db_path).expanduser()
    parent = db_path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        probe = parent / ".doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return DiagResult(
            "数据库目录", "fail", f"{parent} 不可写: {e}", "检查路径权限或改 TIANSHU_DB_PATH"
        )
    exists = "已存在" if db_path.exists() else "首次启动时创建"
    return DiagResult("数据库目录", "ok", f"{db_path}({exists})")


def _check_workspace(settings) -> DiagResult:
    ws = Path(settings.workspace_dir).expanduser()
    if not ws.exists():
        return DiagResult("工作目录", "warn", f"{ws} 不存在", "检查 TIANSHU_WORKSPACE_DIR")
    return DiagResult("工作目录", "ok", str(ws.resolve()))


def _check_port(settings) -> DiagResult:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        occupied = s.connect_ex(("127.0.0.1", settings.port)) == 0
    if not occupied:
        return DiagResult("端口", "ok", f"{settings.port} 空闲")
    # 端口被占:可能是天枢自己在跑(正常),也可能是冲突
    try:
        import httpx

        resp = httpx.get(f"http://127.0.0.1:{settings.port}/health", timeout=2.0)
        if resp.status_code == 200:
            return DiagResult("端口", "ok", f"{settings.port} 上是运行中的天枢服务")
    except Exception:  # noqa: BLE001 - 探测失败即按占用告警
        pass
    return DiagResult(
        "端口", "warn", f"{settings.port} 被其他进程占用", "换 TIANSHU_PORT 或释放该端口"
    )


_OPTIONAL_DEPS = [
    # (启用条件字段, 包名, extras 名)
    ("feishu_app_id", "lark_oapi", "feishu"),
    ("telegram_bot_token", "telegram", "telegram"),
]


def _check_optional_deps(settings) -> list[DiagResult]:
    results: list[DiagResult] = []
    for enable_field, module, extra in _OPTIONAL_DEPS:
        enabled = bool(getattr(settings, enable_field, ""))
        installed = importlib.util.find_spec(module) is not None
        if enabled and not installed:
            results.append(
                DiagResult(
                    f"可选依赖 {extra}",
                    "fail",
                    f"已配置启用但未安装 {module}",
                    f'pip install -e ".[{extra}]" 或 uv sync --extra {extra}',
                )
            )
        elif enabled:
            results.append(DiagResult(f"可选依赖 {extra}", "ok", "已启用且已安装"))
    return results


def _check_secret_key() -> DiagResult:
    import os

    if os.getenv("TIANSHU_SECRET_MASTER_KEY"):
        return DiagResult("凭证主密钥", "ok", "已设置(凭证托管可用)")
    return DiagResult(
        "凭证主密钥",
        "warn",
        "TIANSHU_SECRET_MASTER_KEY 未设置",
        "仅在使用凭证托管(鸿胪寺/秘钥库)时需要",
    )


async def _check_llm(settings) -> DiagResult:
    from tianshu.llm import LLMClient

    client = LLMClient(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        api_base=settings.llm_api_base,
        max_tokens=8,
        timeout=30,
    )
    try:
        await client.chat([{"role": "user", "content": "ping"}])
    except Exception as e:  # noqa: BLE001 - 诊断场景需要兜住一切异常转为结果
        return DiagResult("LLM 连通", "fail", f"调用失败: {e}", "检查 API key / api_base / 网络")
    return DiagResult("LLM 连通", "ok", f"{settings.llm_model} 可用")


def doctor(
    llm: bool = typer.Option(False, "--llm", help="附带一次真实 LLM 最小调用(消耗少量 token)"),
):
    """装机自检:配置 / 数据库 / 端口 / 可选依赖(--llm 附带连通测试)。"""
    from tianshu.config import TianshuSettings

    settings = TianshuSettings()

    results: list[DiagResult] = [
        _check_api_key(settings),
        _check_db(settings),
        _check_workspace(settings),
        _check_port(settings),
        _check_secret_key(),
        *_check_optional_deps(settings),
    ]
    if llm:
        import asyncio

        results.append(asyncio.run(_check_llm(settings)))

    table = Table(title="Tianshu Doctor", show_lines=False)
    table.add_column("检查项", style="bold")
    table.add_column("状态")
    table.add_column("详情")
    table.add_column("建议")
    level_mark = {"ok": _OK, "warn": _WARN, "fail": _FAIL}
    for r in results:
        table.add_row(r.name, level_mark[r.level], r.detail, r.hint)
    console.print(table)

    fails = [r for r in results if r.level == "fail"]
    warns = [r for r in results if r.level == "warn"]
    console.print(
        f"\n{len(results)} 项检查:{len(results) - len(fails) - len(warns)} ok"
        f" / {len(warns)} warn / {len(fails)} fail"
    )
    if fails:
        raise typer.Exit(1)
