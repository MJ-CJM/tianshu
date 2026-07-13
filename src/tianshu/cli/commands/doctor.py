"""tianshu doctor — 只读装机自检（G1.5 版本化结构报告）。

默认只读且零网络（仅可选 loopback live/ready 探测）：不创建目录/文件、
不跑迁移、不构造 LLM 客户端。`--llm` 才做一次真实最小调用（显式联网）。
`--format table|json`；存在 required 失败时以退出码 1 结束。
证据经 allow-list 脱敏：不输出 key 前缀、secret 路径、header、query、
原始异常文本。
"""

from __future__ import annotations

import typer
from rich.console import Console

from tianshu.diagnostics import (
    DoctorCheck,
    DoctorReport,
    _overall,
    _safe_evidence,
    run_doctor_checks,
)

console = Console()


async def _check_llm(settings) -> DoctorCheck:
    """显式 opt-in 的真实连通测试；结果只含稳定布尔证据。"""
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
    except Exception:  # noqa: BLE001 - 原始异常可能携带 URL/header 等敏感物，只转稳定证据
        return DoctorCheck(
            id="provider.connectivity",
            status="fail",
            required=False,
            evidence=_safe_evidence({"reachable": False}),
            remediation="检查 API key / api_base / 网络（原始错误详见服务端日志）",
        )
    return DoctorCheck(
        id="provider.connectivity",
        status="pass",
        required=False,
        evidence=_safe_evidence({"reachable": True}),
    )


def doctor(
    llm: bool = typer.Option(
        False, "--llm", help="附带一次真实 LLM 最小调用(显式联网,消耗少量 token)"
    ),
    output_format: str = typer.Option(
        "table", "--format", help="输出格式: table | json", show_default=True
    ),
):
    """只读装机自检：配置/数据库/迁移/资源/overlay/端口(可选 --llm 连通测试)。"""
    from tianshu.config import TianshuSettings

    if output_format not in ("table", "json"):
        console.print(f"[red]未知格式: {output_format}[/red] (支持 table | json)")
        raise typer.Exit(2)

    settings = TianshuSettings()
    report = run_doctor_checks(settings)

    if llm:
        import asyncio

        extra = asyncio.run(_check_llm(settings))
        checks = (*report.checks, extra)
        report = DoctorReport(
            schema_version=report.schema_version, status=_overall(checks), checks=checks
        )

    if output_format == "json":
        # canonical JSON（sort_keys+compact），供机器消费与黑盒断言
        print(report.to_json())
    else:
        # markup=False：状态列的 [degraded] 与证据文本（可能来自端口上的任意进程）
        # 都不得被 rich 当作样式标签解释/吞掉
        console.print(report.to_table_text(), markup=False, highlight=False)

    if any(c.required and c.status == "fail" for c in report.checks):
        raise typer.Exit(1)
