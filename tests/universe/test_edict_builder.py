"""演化 edict 构建器测试:诊断提案 → keqing:pi 需求规格 + 三重验收物。"""

from tianshu.universe.edict_builder import build_evolution_edict


class TestBuildEvolutionEdict:
    def test_routes_to_keqing_pi(self):
        e = build_evolution_edict(target_path="src/foo.py", hypothesis="加缓存")
        assert e.runtime.executor == "keqing:pi"
        assert e.goal == "加缓存"

    def test_acceptance_has_regression_scope_and_rubric(self):
        e = build_evolution_edict(target_path="src/foo.py", hypothesis="H")
        names = {c.name: c for c in e.acceptance.checks}
        assert names["regression"].kind == "bash" and "pytest" in names["regression"].command
        assert names["evolvable_scope"].kind == "bash"
        assert names["hypothesis_fit"].kind == "rubric" and "H" in names["hypothesis_fit"].rubric

    def test_scope_check_enforces_evolvable_allowlist(self):
        e = build_evolution_edict(
            target_path="src/a.py", hypothesis="H", evolvable_paths=("src/a.py", "src/b.py")
        )
        cmd = e.acceptance.checks[1].command
        assert "'src/a.py'" in cmd and "'src/b.py'" in cmd
        assert "out-of-scope change" in cmd and "exit 1" in cmd  # 越界即失败

    def test_constraints_state_evolvable_domain(self):
        e = build_evolution_edict(
            target_path="src/a.py", hypothesis="H", evolvable_paths=("src/a.py",)
        )
        joined = "\n".join(e.constraints)
        assert "演化域" in joined
        assert "不得破坏现有测试" in joined

    def test_context_combines_rationale_and_symptoms(self):
        e = build_evolution_edict(
            target_path="src/a.py", hypothesis="H", rationale="R", failure_symptoms="S"
        )
        assert "R" in e.context and "S" in e.context

    def test_budget_and_follow_up_wired(self):
        e = build_evolution_edict(
            target_path="src/a.py", hypothesis="H", cost_budget_cny=7.5, follow_up_rounds=2
        )
        assert e.runtime.cost_budget_cny == 7.5
        assert e.acceptance.max_outer_iterations == 2

    def test_no_evolvable_paths_falls_back_to_target(self):
        e = build_evolution_edict(target_path="src/only.py", hypothesis="H")
        assert "'src/only.py'" in e.acceptance.checks[1].command
