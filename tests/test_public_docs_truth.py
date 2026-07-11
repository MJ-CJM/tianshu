"""Truth contracts for the public v0.4.2 surface.

These tests intentionally check public promises rather than implementation details.  A
future capability may only move from planned/experimental after its evidence and public
wording are updated together.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.4.2"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _json(relative_path: str) -> dict[str, object]:
    return json.loads(_read(relative_path))


def test_public_version_markers_match_v042() -> None:
    pyproject = tomllib.loads(_read("pyproject.toml"))
    package = _json("web/package.json")
    package_lock = _json("web/package-lock.json")

    init_match = re.search(r'__version__\s*=\s*"([^"]+)"', _read("src/tianshu/__init__.py"))
    fastapi_match = re.search(
        r'FastAPI\([^)]*\bversion="([^"]+)"',
        _read("src/tianshu/app.py"),
        flags=re.DOTALL,
    )

    assert init_match is not None
    assert fastapi_match is not None
    assert {
        pyproject["project"]["version"],
        init_match.group(1),
        fastapi_match.group(1),
        package["version"],
        package_lock["version"],
        package_lock["packages"][""]["version"],
    } == {EXPECTED_VERSION}


def test_capability_matrix_records_maturity_evidence_and_non_guarantees() -> None:
    matrix_path = ROOT / "docs/launch/capability-matrix.md"
    assert matrix_path.is_file(), "public capability matrix is required"
    matrix = matrix_path.read_text(encoding="utf-8")

    expected_columns = [
        "Capability",
        "Maturity",
        "Default",
        "Supported scope",
        "Verified guarantee",
        "Explicit non-guarantees",
        "Evidence",
        "Target gate",
    ]
    header = next(
        (
            [cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in matrix.splitlines()
            if line.lstrip().startswith("| Capability |")
        ),
        None,
    )

    assert header == expected_columns
    table_lines = matrix.splitlines()
    header_index = next(
        index
        for index, line in enumerate(table_lines)
        if line.lstrip().startswith("| Capability |")
    )
    capability_rows: list[list[str]] = []
    for line in table_lines[header_index + 2 :]:
        if not line.lstrip().startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == 8, f"capability row must have 8 columns: {line}"
        capability_rows.append(cells)

    assert capability_rows
    for cells in capability_rows:
        evidence = cells[6]
        assert evidence and evidence != "—", f"missing Evidence for {cells[0]}"
        evidence_links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", evidence)
        assert evidence_links, f"Evidence must link to source or tests for {cells[0]}"

        for target in evidence_links:
            if "://" in target or target.startswith("mailto:"):
                continue
            relative_target = target.split("#", maxsplit=1)[0]
            resolved_target = (
                matrix_path if not relative_target else matrix_path.parent / relative_target
            )
            assert resolved_target.exists(), f"broken Evidence link for {cells[0]}: {target}"

        if cells[1].startswith("Stable"):
            assert any(
                "tests" in Path(target.split("#", maxsplit=1)[0]).parts for target in evidence_links
            ), f"Stable capability requires automated test evidence: {cells[0]}"

    web_im_row = next(cells for cells in capability_rows if cells[0] == "Web 与 IM 裁决入口")
    assert "../../tests/gateway/telegram/test_callback.py" in re.findall(
        r"\[[^\]]+\]\(([^)]+)\)", web_im_row[6]
    )
    migration_row = next(
        cells for cells in capability_rows if cells[0] == "SQLite 迁移账本、升级备份与离线恢复"
    )
    assert migration_row[1] == "Stable (limited) / 稳定（有限边界）"
    assert migration_row[7] == "G0"
    migration_evidence = re.findall(r"\[[^\]]+\]\(([^)]+)\)", migration_row[6])
    assert "../../tests/storage/test_migration_ledger.py" in migration_evidence
    assert "../../tests/storage/test_backup_restore.py" in migration_evidence
    assert "../../tests/test_storage_instance_migration.py" in migration_evidence

    keqing_row = next(
        cells for cells in capability_rows if cells[0] == "Keqing 外部 Claude Code/Codex CLI"
    )
    keqing_evidence = re.findall(r"\[[^\]]+\]\(([^)]+)\)", keqing_row[6])
    assert "../../tests/executor/keqing/test_executor.py" in keqing_evidence
    assert "../../tests/security/test_clean_env.py" in keqing_evidence
    assert "事后结果归一" in keqing_row[4]
    assert "事后最终结果审计" not in keqing_row[4]

    for maturity in (
        "Stable (limited) / 稳定（有限边界）",
        "Experimental / 实验",
        "Planned / 规划",
    ):
        assert maturity in matrix

    for boundary in (
        "trusted local / 可信本地",
        "contained + experimental",
        "action_interception=false",
        "hard_cost_cap=false",
        "pre_run_restore_point=false",
    ):
        assert boundary in matrix


def test_readmes_do_not_overclaim_current_governance_or_evolution() -> None:
    readme_zh = _read("README.md")
    readme_en = _read("README.en.md")

    assert "天枢是一个可治理、可验证、持续成长的自进化 Agent OS" in readme_zh
    english_subtitle = (
        "A governable, verifiable Agent OS designed to learn and evolve continuously."
    )
    assert f"*{english_subtitle}*" in readme_zh
    assert "A governable, verifiable, continuously growing self-evolving Agent OS." not in readme_zh
    natural_english_positioning = (
        "Tianshu is a governable, verifiable Agent OS designed to learn and evolve continuously."
    )
    assert readme_en.count(natural_english_positioning) == 2
    assert "continuously growing self-evolving Agent OS" not in readme_en
    assert "docs/launch/capability-matrix.md" in readme_zh
    assert "docs/launch/capability-matrix.md" in readme_en
    assert "可信本地" in readme_zh and "不得直接暴露到不可信网络" in readme_zh
    assert (
        "trusted local" in readme_en and "must not be exposed to an untrusted network" in readme_en
    )

    banned_zh = (
        "能力强，但始终受控",
        "候选位面小流量探索",
        "自动择优晋升",
        "一条命令沙箱回放",
        "两个方向都在治理框架内",
        "天枢保留全部治理面",
        "每个工具调用都进了账本",
        "成本按 stream-json 归因并受预算熔断",
    )
    banned_en = (
        "under full Tianshu governance",
        "Both directions stay inside the governance frame",
        "Power, always under control",
        "paired sandbox evaluation",
        "auto-promotion",
        "has no complete equivalent on the market",
        "Phone approval",
        "every executor run is snapshotted",
        "candidates explored at low traffic",
        "both inside governance",
        "replays historical tasks in a sandbox",
    )

    for claim in banned_zh:
        assert claim not in readme_zh
    for claim in banned_en:
        assert claim.casefold() not in readme_en.casefold()


def test_canonical_context_separates_long_term_language_from_v042_facts() -> None:
    context = _read("CONTEXT.md")

    assert "长期产品定位" in context
    assert "术语定义不等于 v0.4.2 已实现能力" in context
    assert "docs/launch/capability-matrix.md" in context
    assert "contained + experimental" in context
    assert "当前只路由 champion" in context
    assert "无真实在线 challenger 流量或可信自动晋升" in context
    assert "G1 规划目标" in context
    assert "G4 规划目标" in context

    for stale_current_claim in (
        "接受同一治理契约;实际可强制的裁决、预算、隔离与审计边界由 Capability Manifest 分级披露,强制能力不足时不得派发",
        "自进化以位面为单位赛跑、按 fitness 晋升",
        '"敢放手"承诺的兜底集合',
        "学习用户裁决习惯后对低风险事项自动作出裁决",
        "经效果门(评估提升才生效)的技能自优化闭环",
    ):
        assert stale_current_claim not in context


def test_public_setup_docs_keep_v042_on_a_trusted_local_boundary() -> None:
    readme_zh = _read("README.md")
    readme_en = _read("README.en.md")
    getting_started = _read("docs/usage/getting-started.md")
    frontend_dev = _read("docs/usage/frontend-dev.md")

    assert "最终镜像只含 Python 运行时 + 前端静态文件" not in readme_zh
    assert "两阶段构建会移除运行时中的 Node.js 和 node_modules" in readme_zh
    assert "`~/.tianshu/tianshu.db`" in readme_zh
    assert "has not yet been measured" in readme_en
    assert "Typical monthly cost range and the measurement method are in" not in readme_en

    assert "http://localhost:7999" in getting_started
    assert "http://localhost:3000" not in getting_started
    assert getting_started.count("-p 127.0.0.1:8000:8000") >= 2
    assert "-p 8000:8000" not in getting_started
    assert "v0.4.2 无统一鉴权" in getting_started
    assert "最终镜像只包含 Python 运行时 + 前端静态文件" not in getting_started
    assert "| `TIANSHU_DB_PATH` | `~/.tianshu/tianshu.db` |" in getting_started

    assert "--host 127.0.0.1" in frontend_dev
    assert "--host 0.0.0.0" not in frontend_dev
    assert "v0.4.2 无统一鉴权" in frontend_dev
    assert "禁止使用公网隧道" in frontend_dev
    for public_tunnel_marker in ("cloudflared", "ngrok", "trycloudflare.com"):
        assert public_tunnel_marker not in frontend_dev


def test_active_docs_use_decision_language_and_current_channel_contracts() -> None:
    docs_index = _read("docs/README.md")
    user_guide = _read("docs/usage/user-guide.md")
    glossary = _read("docs/reference/glossary.md")
    feishu_setup = _read("docs/ops/feishu-setup.md")

    assert "launch/capability-matrix.md" in docs_index
    assert "稳定（有限边界）" in docs_index
    assert "部分轮次 checkpoint" in docs_index
    assert "可审批" not in docs_index

    for current_label in ("待裁决工具", "规划裁决", "工具裁决"):
        assert current_label in user_guide
    for stale_label in ("待审批工具", "规划审批", "批红（工具审批）"):
        assert stale_label not in user_guide

    assert "| 裁决 | `Decree`（历史代码名） |" in glossary
    assert "公开界面统一使用“裁决”" in glossary
    assert "| 批红 | `Decree` |" not in glossary

    assert "公开支持路径：命令回复" in feishu_setup
    for command in ("`/approve`", "`/approve edict`", "`/approve always`", "`/reject`"):
        assert command in feishu_setup
    for unsupported_claim in (
        "card.action.trigger",
        "4 个按钮",
        "卡片刷新为灰色",
        "web 端卡片也会被同步刷新",
        "双通道幂等",
    ):
        assert unsupported_claim not in feishu_setup


def test_strategy_index_is_an_archived_snapshot_not_a_current_product_claim() -> None:
    strategy_index = _read("docs/strategy/README.md")

    assert "历史战略快照" in strategy_index
    assert "不代表 v0.4.2 当前能力或当前发布承诺" in strategy_index
    assert "../launch/capability-matrix.md" in strategy_index
    assert "open-source-agent-os-master-roadmap.md" in strategy_index

    for stale_claim in (
        "市场上唯一",
        "自进化敢上生产",
        "双层自进化闭环(独此一家)",
        "🚀 正式宣发(9 月中)→ v0.3.0",
    ):
        assert stale_claim not in strategy_index


def test_docker_example_defaults_to_loopback_and_requires_secure_remote_for_remote_access() -> None:
    readme_zh = _read("README.md")
    docker_section = readme_zh.split("### Docker 部署", maxsplit=1)[1].split("\n### ", maxsplit=1)[
        0
    ]

    assert "-p 127.0.0.1:8000:8000" in docker_section
    assert "-p 8000:8000" not in docker_section
    assert "默认 `trusted-local` 仅信任回环入口" in docker_section
    assert "`secure-remote`" in docker_section
    assert "匿名 REST、WebSocket 与 MCP 会在统一入口被拒绝" in docker_section
    assert "需要远程访问时必须显式启用" in docker_section
    assert "配置 HTTPS 公共地址、精确 Host/Origin、可信反代 CIDR" in docker_section


def test_keqing_tooltips_state_the_contained_experimental_boundary() -> None:
    locale_paths = (
        "web/src/i18n/locales/zh-classic.json",
        "web/src/i18n/locales/zh-modern.json",
        "web/src/i18n/locales/en.json",
    )
    tooltips = [
        _json(path)["form"]["edict"]["tooltip"]["executor"]  # type: ignore[index]
        for path in locale_paths
    ]

    for tooltip in tooltips:
        assert "contained + experimental" in tooltip
        assert "clean-env" in tooltip
        assert "timeout" in tooltip
        assert "full Tianshu governance" not in tooltip
        assert "全程受" not in tooltip

    for tooltip in tooltips[:2]:
        assert "独立工作目录" in tooltip
        assert "事后审计" in tooltip
        assert "不保证事前工具拦截、硬成本上限或运行前恢复点" in tooltip

    assert "independent workspace" in tooltips[2]
    assert "post-run audit" in tooltips[2]
    assert "no pre-tool interception, hard cost cap, or pre-run restore point" in tooltips[2]


def test_launch_materials_describe_only_currently_demoable_channels() -> None:
    checklist = _read("docs/launch/checklist.md")
    storyboard = _read("docs/launch/demo-storyboards.md")
    launch_index = _read("docs/launch/README.md")
    architecture_post = _read("docs/launch/blog-architecture.md")
    metaphor_map = _read("docs/launch/metaphor-map.md")
    cost_baseline = _read("docs/launch/cost-baseline.md")
    decisions = _read("docs/strategy/DECISIONS.md")

    assert "当前版本：0.4.2" in checklist
    assert "G1 Developer Preview" in checklist
    assert "G5 正式宣发" in checklist
    assert "v0.3.0" not in checklist
    assert "年末 v0.4" not in checklist

    assert "飞书：命令回复" in storyboard
    assert "Telegram：按钮" in storyboard
    assert "contained + experimental" in storyboard
    assert "手机" not in storyboard
    assert "飞书弹出" not in storyboard
    assert "飞书机器人配好、能收审批卡片" not in storyboard
    assert "每个工具调用都进了账本" not in storyboard

    active_launch_material = launch_index + architecture_post + metaphor_map + cost_baseline
    for stale_claim in (
        "v0.3.0 正式发布",
        "HN 留年末 v0.4",
        "手机批红",
        "手机上点准",
        "放手四保险",
        "隔离沙箱",
        "两个方向都在治理框架内",
        "成本按 stream-json 归因并受预算熔断",
    ):
        assert stale_claim not in active_launch_material
    assert "capability-matrix.md" in launch_index
    assert "capability-matrix.md" in architecture_post
    assert "| 裁决 | Decision |" in metaphor_map
    assert "best-effort" in cost_baseline
    assert "可能超调" in cost_baseline
    assert "litellm.Router" not in cost_baseline

    assert "批准或交付不等于稳定" in decisions
    assert "capability-matrix.md" in decisions
    assert "contained + experimental" in decisions
    assert "当前不提供手机 App" in decisions
    assert "MCP 入口能力有限" in decisions
