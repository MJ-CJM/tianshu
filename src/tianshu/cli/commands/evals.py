"""tianshu evals — 平台级回归评测与失败归因(迭代 2「证明」)。

一条命令产出评测报告:`tianshu evals run`。评测是离线活(起沙箱逐条回放,
分钟级、花 LLM 钱),入口只在 CLI;web/API 只读台账。评估凭证隔离:
TIANSHU_EVAL_LLM_* 有值时注入沙箱(不打主配额),否则沙箱继承主凭证。
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer()
console = Console()


def _build_runner():
    """本地直构(离线跑批,不依赖运行中的天枢服务)。"""
    import tianshu
    from tianshu.config import TianshuSettings
    from tianshu.evals import PlatformEvalRunner
    from tianshu.executor.execution_gateway import ExecutionGateway
    from tianshu.storage import Storage
    from tianshu.universe.eval_harness import EvalHarness
    from tianshu.universe.execution import UniverseExecutionContextFactory
    from tianshu.universe.sandbox import SandboxRunner

    settings = TianshuSettings()
    storage = Storage(settings.db_path)
    storage.init_db()
    eval_base_env: dict[str, str] = {}
    if settings.eval_llm_api_key:
        eval_base_env["TIANSHU_LLM_API_KEY"] = "${settings:eval_llm_api_key}"
    elif settings.llm_api_key:
        eval_base_env["TIANSHU_LLM_API_KEY"] = "${settings:llm_api_key}"
    api_base = settings.eval_llm_api_base or settings.llm_api_base
    model = settings.eval_llm_model or settings.llm_model
    if api_base:
        eval_base_env["TIANSHU_LLM_API_BASE"] = api_base
    if model:
        eval_base_env["TIANSHU_LLM_MODEL"] = model
    setting_secrets = {
        "settings:eval_llm_api_key": settings.eval_llm_api_key,
        "settings:llm_api_key": settings.llm_api_key,
    }
    gateway = ExecutionGateway(
        secret_resolver=lambda ref: setting_secrets.get(ref) or None,
    )
    sandbox = SandboxRunner(
        gateway,
        context_factory=UniverseExecutionContextFactory(security_mode=settings.security_mode),
    )
    harness = EvalHarness(storage, sandbox, base_env=eval_base_env)
    repo_root = Path(tianshu.__file__).resolve().parents[2]
    return PlatformEvalRunner(storage, harness, repo_root=repo_root), storage, settings


def _print_run_report(run: dict) -> None:
    fitness = run.get("fitness", {})
    title = f"Eval Run {run['id'][:10]}…"
    if run.get("eval_set_name"):
        title += f"(set={run['eval_set_name']})"
    table = Table(title=title)
    table.add_column("指标", style="bold")
    table.add_column("值", justify="right")
    table.add_row("综合分 score", f"{fitness.get('score', 0):.4f}")
    delta = run.get("delta_vs_prev")
    if delta is not None:
        color = "green" if delta >= 0 else "red"
        table.add_row("Δ vs 上次(同集)", f"[{color}]{delta:+.4f}[/{color}]")
    table.add_row("成功率", f"{fitness.get('success_rate', 0):.2%}")
    table.add_row("审计通过率", f"{fitness.get('audit_rate', 0):.2%}")
    table.add_row("重试分", f"{fitness.get('retry_score', 0):.4f}")
    table.add_row("成本分", f"{fitness.get('cost_score', 0):.4f}")
    table.add_row("样本数", str(run.get("n", 0)))
    if run.get("truncated"):
        table.add_row("预算截断", "[yellow]是[/yellow]")
    table.add_row("评测对象", run.get("target", ""))
    console.print(table)

    goal_results = run.get("goal_results", [])
    if goal_results:
        gt = Table(title="逐条结果")
        gt.add_column("#", justify="right")
        gt.add_column("goal")
        gt.add_column("状态")
        gt.add_column("失败归因")
        for i, g in enumerate(goal_results, 1):
            status = g.get("status", "")
            style = {"completed": "green", "approved": "green", "failed": "red"}.get(status, "")
            instr = (g.get("instruction") or "")[:60]
            gt.add_row(
                str(i),
                instr,
                f"[{style}]{status}[/{style}]" if style else status,
                g.get("failure_reason") or "",
            )
        console.print(gt)

    dist = run.get("failure_distribution", [])
    if dist:
        dt = Table(title="失败归因分布")
        dt.add_column("原因")
        dt.add_column("次数", justify="right")
        for d in dist:
            dt.add_row(d["reason"], str(d["count"]))
        console.print(dt)


@app.command("run")
def evals_run(
    set_name: str = typer.Option(None, "--set", help="使用已保存的评测集(否则现场分层混采)"),
    size: int = typer.Option(8, "--size", help="现场采样时的评测集大小"),
    timeout: int = typer.Option(300, "--timeout", help="单条 goal 超时(秒)"),
    budget: float = typer.Option(None, "--budget", help="本次评测预算上限(CNY),触顶截断"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """跑一次平台回归评测并产出报告(沙箱回放,分钟级)。"""
    runner, _storage, settings = _build_runner()
    import os

    if not settings.eval_llm_api_key and not (
        settings.llm_api_key or os.environ.get("TIANSHU_LLM_API_KEY")
    ):
        console.print(
            "[red]✗ 无可用 LLM 凭证[/red]:请设置 TIANSHU_EVAL_LLM_API_KEY(评估专用,推荐)"
            "或 TIANSHU_LLM_API_KEY"
        )
        raise typer.Exit(1)

    console.print("[dim]启动沙箱回放评测集(离线跑批,分钟级)…[/dim]")
    try:
        run = runner.run(set_name=set_name, size=size, goal_timeout_s=timeout, budget_cny=budget)
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1) from None
    if as_json:
        console.print_json(json.dumps(run, ensure_ascii=False))
        return
    _print_run_report(run)


@app.command("sample")
def evals_sample(
    name: str = typer.Argument(..., help="评测集名称"),
    size: int = typer.Option(8, "--size", help="采样条数"),
):
    """从历史 memorial 分层混采一份评测集并保存(固化为可重复回归集)。"""
    runner, _storage, _settings = _build_runner()
    try:
        goals = runner.sample_and_save(name, size=size)
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1) from None
    console.print(f"[green]✓[/green] 评测集 [bold]{name}[/bold] 已保存({len(goals)} 条):")
    for i, g in enumerate(goals, 1):
        console.print(f"  {i}. {g[:80]}")


@app.command("sets")
def evals_sets():
    """列出已保存的评测集。"""
    _runner, storage, _settings = _build_runner()
    sets = storage.list_eval_sets()
    if not sets:
        console.print("[dim]暂无评测集;用 `tianshu evals sample <name>` 创建[/dim]")
        return
    table = Table(title="评测集")
    table.add_column("名称", style="bold")
    table.add_column("条数", justify="right")
    table.add_column("来源")
    table.add_column("创建时间")
    for s in sets:
        table.add_row(s["name"], str(len(s["goals"])), s["source"], s["created_at"][:19])
    console.print(table)


@app.command("runs")
def evals_runs(limit: int = typer.Option(20, "--limit")):
    """列出历史评测运行。"""
    _runner, storage, _settings = _build_runner()
    runs = storage.list_platform_eval_runs(limit=limit)
    if not runs:
        console.print("[dim]暂无评测运行;用 `tianshu evals run` 跑一次[/dim]")
        return
    table = Table(title="评测运行台账")
    table.add_column("ID")
    table.add_column("评测集")
    table.add_column("score", justify="right")
    table.add_column("Δ", justify="right")
    table.add_column("n", justify="right")
    table.add_column("时间")
    for r in runs:
        delta = r.get("delta_vs_prev")
        delta_s = f"{delta:+.4f}" if delta is not None else "—"
        table.add_row(
            r["id"][:10] + "…",
            r.get("eval_set_name") or "(临时采样)",
            f"{r['fitness'].get('score', 0):.4f}",
            delta_s,
            str(r["n"]),
            r["created_at"][:19],
        )
    console.print(table)


@app.command("show")
def evals_show(run_id: str = typer.Argument(..., help="评测运行 ID(可用前缀)")):
    """查看一次评测运行的完整报告。"""
    from tianshu.evals import aggregate_failure_distribution

    _runner, storage, _settings = _build_runner()
    run = storage.get_platform_eval_run(run_id)
    if run is None:
        # 前缀匹配容错
        for r in storage.list_platform_eval_runs(limit=200):
            if r["id"].startswith(run_id):
                run = storage.get_platform_eval_run(r["id"])
                break
    if run is None:
        console.print(f"[red]✗ 未找到评测运行:{run_id}[/red]")
        raise typer.Exit(1)
    run["failure_distribution"] = aggregate_failure_distribution(run.get("goal_results", []))
    _print_run_report(run)


@app.command("failures")
def evals_failures(
    days: int = typer.Option(None, "--days", help="仅统计最近 N 天"),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """主库失败归因分布(失败的 memorial 按 failure_reason 聚合)。"""
    _runner, storage, _settings = _build_runner()
    dist = storage.failure_reason_distribution(days=days)
    if as_json:
        console.print_json(json.dumps(dist, ensure_ascii=False))
        return
    if not dist:
        console.print("[green]✓ 没有失败记录[/green]")
        return
    title = "失败归因分布" + (f"(近 {days} 天)" if days else "(全部)")
    table = Table(title=title)
    table.add_column("原因", style="bold")
    table.add_column("次数", justify="right")
    table.add_column("最近一次")
    for d in dist:
        table.add_row(d["reason"], str(d["count"]), (d["last_seen"] or "")[:19])
    console.print(table)


@app.command("backfill")
def evals_backfill(
    reclassify: bool = typer.Option(
        False, "--re-classify", help="全量重分类(分类器升级后使用;默认只补未分类行)"
    ),
):
    """按失败分类学回填历史 failed memorial 的 failure_reason。

    新库/升级库在启动迁移时已自动回填未分类行;本命令用于分类器规则
    升级后的全量重分类。
    """
    _runner, storage, _settings = _build_runner()
    updated = storage.backfill_failure_reasons(reclassify=reclassify)
    mode = "全量重分类" if reclassify else "补齐未分类行"
    console.print(f"[green]✓[/green] {mode}:更新 {updated} 行")
