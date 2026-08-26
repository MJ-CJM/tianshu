"""tianshu serve —— 安装后的默认启动入口（Web UI + API 同端口）。

此前使用者必须自行拼 `uvicorn tianshu.app:create_app --factory`，等于把 ASGI
细节和静态资产位置暴露到命令行。serve 把这些收进 CLI：host/port 取自
TianshuSettings，静态资产由 create_app 自行回落到打包 Web 载荷，无需设置
TIANSHU_STATIC_DIR。

--host/--port 先回灌成环境变量再重建 TianshuSettings：命令行不能成为绕过
security_mode loopback 校验的第二条路径，且 reload 子进程与 create_app 读到
的配置与实际绑定值同源。
"""

from __future__ import annotations

import os

import typer
from rich.console import Console

console = Console()


def serve(
    host: str | None = typer.Option(
        None, "--host", help="监听地址(默认取 TIANSHU_HOST,未设置则 127.0.0.1)"
    ),
    port: int | None = typer.Option(
        None, "--port", help="监听端口(默认取 TIANSHU_PORT,未设置则 8000)"
    ),
    system_snapshot: str | None = typer.Option(
        None,
        "--system-snapshot",
        help=(
            "指定启动时对照的 64 位小写 SystemSnapshot 摘要；"
            "仅在 TIANSHU_SYSTEM_SNAPSHOT_STRICT=true 时要求匹配"
        ),
    ),
    reload: bool = typer.Option(False, "--reload", help="代码变更自动重启(开发用)"),
):
    """启动天枢服务:浏览器打开提示的地址即可使用 Web UI。"""
    import uvicorn
    from pydantic import ValidationError

    from tianshu.config import TianshuSettings

    if host is not None:
        os.environ["TIANSHU_HOST"] = host
    if port is not None:
        os.environ["TIANSHU_PORT"] = str(port)
    if system_snapshot is not None:
        os.environ["TIANSHU_SYSTEM_SNAPSHOT_TARGET"] = system_snapshot

    try:
        settings = TianshuSettings()
    except ValidationError as exc:
        console.print("[red]配置校验失败,服务未启动:[/red]")
        for error in exc.errors():
            # 只回显 msg:errors() 的 input 字段可能携带 API key 等配置原值
            console.print(f"  - {error['msg']}")
        raise typer.Exit(1) from None

    console.print(f"天枢启动中 → [bold]http://{settings.host}:{settings.port}[/bold]")
    uvicorn.run(
        "tianshu.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=reload,
    )
