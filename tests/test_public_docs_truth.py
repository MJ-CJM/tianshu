"""Truth contracts for the public v0.4.2 surface.

These tests intentionally check public promises rather than implementation details.  A
future capability may only move from planned/experimental after its evidence and public
wording are updated together.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.4.2"
PUBLIC_PREVIEW_DOCS = (
    "README.md",
    "README.en.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "docs/launch/README.md",
    "docs/launch/capability-matrix.md",
    "docs/launch/checklist.md",
    "docs/launch/demo-storyboards.md",
    "docs/usage/lean-developer-preview.md",
)
TASK4_TRUTH_DOCS = (*PUBLIC_PREVIEW_DOCS, ".superpowers/sdd/closure-task-4-report.md")
EXACT_CAPABILITY_STATES = {
    "remote MCP": "disabled",
    "open stdio MCP": "disabled",
    "official container": "deferred",
    "PyPI": "deferred",
    "GHCR": "deferred",
    "OpenHands": "external_pending",
    "ROI": "external_pending",
    "cost calibration": "external_pending",
    "full G4": "external_pending",
    "full G5": "deferred",
}


def _read(relative_path: str) -> str:
    path = ROOT / relative_path
    assert path.is_file(), f"required public truth file is missing: {relative_path}"
    return path.read_text(encoding="utf-8")


def _json(relative_path: str) -> dict[str, object]:
    return json.loads(_read(relative_path))


def _guide_bash_commands() -> list[str]:
    commands: list[str] = []
    pending = ""
    in_bash = False
    for raw_line in _read("docs/usage/lean-developer-preview.md").splitlines():
        if raw_line == "```bash":
            in_bash = True
            continue
        if in_bash and raw_line == "```":
            in_bash = False
            continue
        if not in_bash or not raw_line.strip():
            continue
        line = raw_line.strip()
        pending = f"{pending} {line}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        commands.append(pending)
        pending = ""
    assert not pending
    return commands


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
    assert "Ubuntu + Python 3.12" in readme_zh
    assert "Ubuntu + Python 3.12" in readme_en
    assert "单机、单节点" in readme_zh and "宿主机管理员" in readme_zh
    assert "single-host, single-node" in readme_en and "host administrator" in readme_en

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


def test_public_setup_docs_expose_only_source_and_exact_wheel_installation() -> None:
    readme_zh = _read("README.md")
    readme_en = _read("README.en.md")
    guide_path = ROOT / "docs/usage/lean-developer-preview.md"

    assert guide_path.is_file(), "the Lean Developer Preview usage guide is required"
    guide = guide_path.read_text(encoding="utf-8")
    combined = readme_zh + readme_en + guide

    assert "源码安装" in guide and "Source checkout" in guide
    assert "exact Wheel" in guide
    assert ".build-venv/bin/python -m build --wheel --outdir dist/lean-preview" in guide
    assert "--only-binary=:all:" in guide
    assert "uv build" not in guide
    assert "Ubuntu + Python 3.12" in guide
    assert "Darwin/arm64/Python 3.12.12" in guide
    assert "首个正式支持目标" in guide
    assert "本地验证环境" in guide

    for unsupported_distribution in (
        "docker build -t tianshu",
        "pip install tianshu",
        "ghcr.io/",
    ):
        assert unsupported_distribution not in combined


def test_lean_preview_guide_bootstraps_every_referenced_environment_and_tool() -> None:
    commands = _guide_bash_commands()
    shell = "\n".join(commands)
    syntax = subprocess.run(
        ["bash", "-n"],
        input=shell,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr

    created_venvs: set[str] = set()
    build_ready_venvs: set[str] = set()
    exact_wheel_venvs: set[str] = set()
    assigned_variables = {"PWD", "TMPDIR"}

    for command in commands:
        variable_references = {
            next(part for part in match.groups() if part)
            for match in re.finditer(
                r"\$(?:([A-Z][A-Z0-9_]*)|\{([A-Z][A-Z0-9_]*)(?::[^}]*)?\})", command
            )
        }
        assignments = {
            match.group(1)
            for match in re.finditer(r"(?:^|\s)(?:export\s+)?([A-Z][A-Z0-9_]*)=", command)
        }
        assert variable_references - assigned_variables - assignments == set(), (
            f"shell variable referenced before assignment in: {command}"
        )

        create_match = re.search(r"python3\.12 -m venv (\.[a-z-]+venv)\b", command)
        if create_match:
            created_venvs.add(create_match.group(1))

        referenced_venvs = set(re.findall(r"(\.[a-z-]+venv)/bin/", command))
        assert referenced_venvs <= created_venvs, f"venv referenced before creation: {command}"

        build_bootstrap = re.search(
            r"(\.[a-z-]+venv)/bin/python -m pip install [\"']?build==[0-9]+(?:\.[0-9]+)+[\"']?",
            command,
        )
        if build_bootstrap:
            build_ready_venvs.add(build_bootstrap.group(1))

        build_command = re.search(r"(\.[a-z-]+venv)/bin/python -m build\b", command)
        if build_command:
            assert build_command.group(1) in build_ready_venvs, (
                f"build frontend was not bootstrapped: {command}"
            )

        exact_install = re.search(
            r"(\.[a-z-]+venv)/bin/python -m pip install --only-binary=:all:", command
        )
        if exact_install:
            exact_wheel_venvs.add(exact_install.group(1))

        if "bin/tianshu-lean-demo" in command:
            runner_venv = re.search(r"(\.[a-z-]+venv)/bin/tianshu-lean-demo", command)
            assert runner_venv is not None
            assert runner_venv.group(1) in exact_wheel_venvs, (
                "public runner must use the exact-Wheel environment"
            )

        if "scripts/verify_lean_preview_evidence.py" in command:
            verifier_venv = re.search(r"(\.[a-z-]+venv)/bin/python", command)
            assert verifier_venv is not None
            assert verifier_venv.group(1) in exact_wheel_venvs, (
                "strict verifier must run from the exact-Wheel environment"
            )

        assigned_variables.update(assignments)


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


def test_lean_preview_golden_demo_has_one_installed_runner_and_strict_verifier() -> None:
    guide = _read("docs/usage/lean-developer-preview.md")
    all_public_preview_docs = "\n".join(_read(path) for path in PUBLIC_PREVIEW_DOCS)

    assert all_public_preview_docs.count("tianshu-lean-demo \\") == 1
    assert guide.count("tianshu-lean-demo \\") == 1
    assert ".preview-venv/bin/tianshu-lean-demo" in guide
    assert "scripts/verify_lean_preview_evidence.py" in guide
    assert '--expected-source-commit "$SOURCE_COMMIT"' in guide
    assert '--expected-wheel-sha256 "$WHEEL_SHA256"' in guide
    assert "20260718T072917Z-b27f525fe4ef" in guide
    assert "b27f525fe4eff52a24f0c7769125bc158097e7de" in guide
    assert "81ec17b9818e67ac6046fb0e1ab62d13606fcaa5af14141ae4d311179bc10fef" in guide


def test_lean_preview_public_docs_use_evidence_states_and_current_capabilities() -> None:
    matrix = _read("docs/launch/capability-matrix.md")
    launch_index = _read("docs/launch/README.md")
    checklist = _read("docs/launch/checklist.md")
    storyboard = _read("docs/launch/demo-storyboards.md")
    guide = _read("docs/usage/lean-developer-preview.md")
    security = _read("SECURITY.md")
    contributing = _read("CONTRIBUTING.md")
    combined = "\n".join((matrix, launch_index, checklist, storyboard, guide))

    for state in (
        "implemented",
        "disabled",
        "deferred",
        "experimental",
        "external_pending",
        "user_approval_pending",
    ):
        assert f"`{state}`" in combined

    for implemented_capability in (
        "SystemAudit",
        "MCP persisted secret mappings",
        "durable governance",
        "Evidence Bundle v1",
        "中枢总览",
        "敕令详情",
        "演化中心",
        "Lean Core evolution",
    ):
        assert implemented_capability in combined

    for deferred_capability in (
        "remote MCP",
        "open stdio MCP",
        "official container",
        "PyPI",
        "GHCR",
        "OpenHands",
        "ROI",
        "cost calibration",
        "full G4",
        "full G5",
    ):
        assert deferred_capability in combined

    assert "`publication_status`: `not_authorized`" in combined
    assert "single-node SQLite" in security
    assert "host administrator" in security
    assert "remote MCP" in security and "`disabled`" in security
    assert "open stdio MCP" in security and "`disabled`" in security
    assert "TDD" in contributing
    assert "migration freeze" in contributing
    assert "truth states" in contributing
    assert "no-mock UI" in contributing


def test_task4_docs_map_each_deferred_capability_to_one_exact_truth_state() -> None:
    for relative_path in TASK4_TRUTH_DOCS:
        normalized = " ".join(_read(relative_path).split())
        for ambiguous in (
            "`external_pending` or `deferred`",
            "`deferred` or `external_pending`",
            "`external_pending` 或 `deferred`",
            "`deferred` 或 `external_pending`",
            "full G4/full G5",
            "完整 G4/G5",
        ):
            assert ambiguous not in normalized, f"ambiguous state in {relative_path}: {ambiguous}"

        for capability, expected_state in EXACT_CAPABILITY_STATES.items():
            if capability.casefold() not in normalized.casefold():
                continue
            assert re.search(
                rf"{re.escape(capability)}.{{0,180}}`{expected_state}`",
                normalized,
                flags=re.IGNORECASE,
            ), f"{capability} must map to {expected_state} in {relative_path}"


def test_lean_preview_public_docs_preserve_exact_desktop_brand_facts() -> None:
    guide = _read("docs/usage/lean-developer-preview.md")
    storyboard = _read("docs/launch/demo-storyboards.md")
    readme_zh = _read("README.md")
    readme_en = _read("README.en.md")
    combined = "\n".join((guide, storyboard, readme_zh, readme_en))

    assert "天枢是一个可治理、可验证、持续成长的自进化 Agent OS。" in combined
    assert "web/public/brand.png" in combined
    assert "3f2bb6cfdcac70092fce3a9b8b534c4a0627f444cb9db38a9651087688ace799" in combined
    assert "成功只有一个——按照自己的方式，去度过人生。" in combined
    assert "彩蛋 / 通用 / English / 实时 / 通政" in combined
    assert "十四部门" in combined
    assert "desktop Web only" in combined
    assert "mobile" in combined and "`deferred`" in combined


def test_lean_preview_public_docs_do_not_make_release_or_safety_overclaims() -> None:
    combined = "\n".join(_read(path) for path in PUBLIC_PREVIEW_DOCS)

    for prohibited in (
        "1.0 ready",
        "exactly once",
        "exactly-once",
        "secure sandbox",
        "市场唯一",
        "同类唯一",
        "the only Agent OS",
        "unique Agent OS",
    ):
        assert prohibited.casefold() not in combined.casefold()


def test_lean_preview_public_document_links_resolve_locally() -> None:
    for relative_path in PUBLIC_PREVIEW_DOCS:
        document_path = ROOT / relative_path
        assert document_path.is_file(), f"missing public document: {relative_path}"
        document = document_path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document):
            if "://" in target or target.startswith(("mailto:", "#")):
                continue
            local_target = target.split("#", maxsplit=1)[0]
            if not local_target:
                continue
            assert (document_path.parent / local_target).exists(), (
                f"broken local link in {relative_path}: {target}"
            )


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


def test_launch_materials_describe_only_the_verified_desktop_golden_story() -> None:
    checklist = _read("docs/launch/checklist.md")
    storyboard = _read("docs/launch/demo-storyboards.md")
    launch_index = _read("docs/launch/README.md")
    architecture_post = _read("docs/launch/blog-architecture.md")
    metaphor_map = _read("docs/launch/metaphor-map.md")
    cost_baseline = _read("docs/launch/cost-baseline.md")
    decisions = _read("docs/strategy/DECISIONS.md")

    assert "当前版本：0.4.2" in checklist
    assert "Lean Developer Preview Candidate" in checklist
    assert "G5 正式宣发" in checklist
    assert "v0.3.0" not in checklist
    assert "年末 v0.4" not in checklist

    assert "桌面 Web" in storyboard
    assert "13 步" in storyboard
    assert "tianshu-lean-demo" not in storyboard
    assert "飞书" not in storyboard
    assert "Telegram" not in storyboard
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


def test_s2_lean_threat_model_states_only_proven_and_deferred_security() -> None:
    threat_model_path = ROOT / "docs/security/lean-preview-threat-model.md"
    assert threat_model_path.is_file(), "the S2 Lean public threat model is required"
    threat_model = threat_model_path.read_text(encoding="utf-8")
    normalized_threat_model = " ".join(threat_model.split())

    for status in ("Proven", "Disabled by design", "Deferred", "Not proven"):
        assert status in threat_model

    for current_truth in (
        "source checkout and exact Wheel are the current official installation paths",
        "SystemAudit tamper-evident chain: implemented",
        "MCP persisted secret mappings at rest: implemented as ciphertext",
        "secure-remote remote MCP: disabled and deferred",
        "stdio persistent exact grant and executable binding: deferred",
        "current Lean stdio boundary is an explicit non-empty `tools.include` allowlist",
        "container images, PyPI, GHCR, and artifact signing: deferred",
        "single-node SQLite",
        "exactly one mode `0600` `legacy-sensitive` recovery backup",
        "protect it from disclosure and remove it manually after recovery",
    ):
        assert current_truth in normalized_threat_model

    for overclaim in (
        "status: full G1.6 passed",
        "full SSRF protection: proven",
        "DNS pinning: implemented",
        "remote MCP security: proven",
        "official container: published",
        "PyPI: published",
        "GHCR: published",
        "artifact signing: implemented",
    ):
        assert overclaim not in threat_model


def test_s2_lean_public_docs_preserve_mcp_and_recovery_boundaries() -> None:
    matrix = _read("docs/launch/capability-matrix.md")
    credentials = _read("docs/ops/credentials.md")
    mcp_example = _read("docs/ops/mcp_servers.yaml.example")

    assert "SystemAudit tamper-evident chain" in matrix
    assert "MCP persisted secret mappings" in matrix
    assert "Implemented / 已实现" in matrix
    assert "single-node SQLite" in matrix
    assert "remote MCP" in matrix and "Disabled / Deferred" in matrix
    assert "stdio exact grant / executable binding" in matrix and "Deferred / 延期" in matrix
    assert "container / PyPI / GHCR / signing" in matrix and "Deferred / 延期" in matrix

    assert "legacy-sensitive recovery backup" in credentials
    assert "exactly one" in credentials
    assert "`0600`" in credentials
    assert "manual protection and cleanup" in credentials

    filesystem_example = mcp_example.split("\n  filesystem:", maxsplit=1)[1].split(
        "\n  github:", maxsplit=1
    )[0]
    remote_example = mcp_example.split("\n  github:", maxsplit=1)[1].split("\n# -----", maxsplit=1)[
        0
    ]
    normalized_filesystem_example = " ".join(filesystem_example.split())
    parsed_servers = yaml.safe_load(mcp_example)["mcp_servers"]
    assert "enabled: false" in filesystem_example
    assert parsed_servers["filesystem"]["tools"]["include"]
    assert "non-empty tools.include" in normalized_filesystem_example
    assert "enabled: false" in remote_example
    assert "secure-remote" in remote_example
    assert "deferred" in remote_example
