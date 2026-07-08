"""修撰效果门(迭代 6「演化 2.0」,ADR-0007)——修撰后须过配对评估提升才生效,失败安全。"""

from __future__ import annotations

from tianshu.skills.curator import SkillCurator


def _curator(storage, config_manager, tmp_path, evaluator=None):
    return SkillCurator(
        llm_client=None,
        loader=None,
        metrics_store=None,
        storage=storage,
        config_manager=config_manager,
        runtime_dir=tmp_path,
        effect_evaluator=evaluator,
    )


class TestEffectGate:
    async def test_gate_off_passes_through(self, storage, config_manager, tmp_path):
        config_manager.update_agent_config(skill_effect_gate_enabled=False)
        cur = _curator(storage, config_manager, tmp_path, evaluator=_const(0.5))
        ok, reason = await cur._effect_gate("s", "old", "new")
        assert ok is True and reason == "gate_off"

    async def test_gate_on_no_evaluator_passes(self, storage, config_manager, tmp_path):
        config_manager.update_agent_config(skill_effect_gate_enabled=True)
        cur = _curator(storage, config_manager, tmp_path, evaluator=None)
        ok, reason = await cur._effect_gate("s", "old", "new")
        assert ok is True and reason == "gate_off"

    async def test_improvement_activates(self, storage, config_manager, tmp_path):
        config_manager.update_agent_config(
            skill_effect_gate_enabled=True, skill_effect_gate_margin=0.05
        )
        cur = _curator(storage, config_manager, tmp_path, evaluator=_const(0.12))
        ok, reason = await cur._effect_gate("s", "old", "new")
        assert ok is True and reason.startswith("delta=")

    async def test_no_improvement_quarantines(self, storage, config_manager, tmp_path):
        config_manager.update_agent_config(
            skill_effect_gate_enabled=True, skill_effect_gate_margin=0.05
        )
        cur = _curator(storage, config_manager, tmp_path, evaluator=_const(0.01))
        ok, reason = await cur._effect_gate("s", "old", "new")
        assert ok is False and "no_improvement" in reason

    async def test_eval_unavailable_quarantines(self, storage, config_manager, tmp_path):
        config_manager.update_agent_config(skill_effect_gate_enabled=True)
        cur = _curator(storage, config_manager, tmp_path, evaluator=_const(None))
        ok, reason = await cur._effect_gate("s", "old", "new")
        assert ok is False and reason == "eval_unavailable"

    async def test_eval_error_quarantines(self, storage, config_manager, tmp_path):
        config_manager.update_agent_config(skill_effect_gate_enabled=True)

        async def _boom(name, old, new):
            raise RuntimeError("eval blew up")

        cur = _curator(storage, config_manager, tmp_path, evaluator=_boom)
        ok, reason = await cur._effect_gate("s", "old", "new")
        assert ok is False and reason == "eval_error"


def _const(value):
    async def _evaluator(name, old_md, new_md):
        return value

    return _evaluator
