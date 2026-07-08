"""SkillEffectEvaluator —— 修撰效果门的配对评估器(迭代 6「演化 2.0」,ADR-0007)。

复用与位面同源的 EvalHarness.evaluate_paired:基线 = 现网技能库,变体 = 只替换被修撰的
那一个技能的技能库(拷贝现网 + 覆写单文件),经 TIANSHU_RUNTIME_SKILLS_DIR 重定向让评估
子进程加载变体库。返回 delta(变体相对基线的适应度提升);任何一步缺失/异常均返回 None
(评估未能进行),由 curator 的效果门据 fail-safe「提升才生效」处置——不激活未获证提升的修撰。

依赖(eval_harness / code_variant_store)在 wire_universe 才装配,晚于 curator,故本类持有
app 引用、调用时才从 app.state 惰性取,取不到即 None(降级安全)。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SkillEffectEvaluator:
    def __init__(self, app: Any, config_manager: Any, loader: Any) -> None:
        self._app = app
        self._config = config_manager
        self._loader = loader

    async def __call__(self, name: str, old_md: str, new_md: str) -> float | None:
        harness = getattr(self._app.state, "code_eval_harness", None)
        code_store = getattr(self._app.state, "code_variant_store", None)
        live_dir = getattr(self._loader, "user_dir", None)
        if harness is None or code_store is None or not live_dir:
            return None
        cfg = self._config.agent_config
        size = getattr(cfg, "skill_effect_gate_eval_set_size", 10)
        eval_set = harness.select_eval_set(size)
        if not eval_set:
            return None
        budget = getattr(cfg, "code_variant_eval_budget_cny", None)
        try:
            return await asyncio.to_thread(
                self._run_paired,
                harness,
                code_store.repo_root,
                Path(live_dir),
                name,
                new_md,
                eval_set,
                budget,
            )
        except Exception:  # noqa: BLE001
            logger.exception("[EFFECT-GATE] paired eval failed for %s", name)
            return None

    @staticmethod
    def _run_paired(
        harness: Any,
        repo_root: Any,
        live_dir: Path,
        name: str,
        new_md: str,
        eval_set: list[str],
        budget: float | None,
    ) -> float | None:
        """构造变体技能库(现网拷贝 + 覆写单技能),对基线跑配对评估,取 delta。"""
        with tempfile.TemporaryDirectory(prefix="tianshu-skilleval-") as tmp:
            variant = Path(tmp) / "skills"
            shutil.copytree(live_dir, variant)
            skill_file = variant / name / "SKILL.md"
            skill_file.parent.mkdir(parents=True, exist_ok=True)
            skill_file.write_text(new_md, encoding="utf-8")
            paired = harness.evaluate_paired(
                repo_root,
                eval_set=eval_set,
                baseline_worktree=repo_root,
                variant_env={"TIANSHU_RUNTIME_SKILLS_DIR": str(variant)},
                budget_cny=budget,
            )
        return paired["delta"]
