from tianshu.universe.fitness import compute_fitness


def test_all_success_scores_high():
    f = compute_fitness({"total": 10, "success": 10, "audited": 10, "audit_pass": 10,
                         "retries": 0, "cost": 0.0, "feedback": 5})
    assert f["score"] > 0.8 and f["samples"] == 10


def test_all_fail_scores_low():
    f = compute_fitness({"total": 10, "success": 0, "audited": 10, "audit_pass": 0,
                         "retries": 20, "cost": 5.0, "feedback": -5})
    assert f["score"] < 0.3


def test_zero_samples_no_crash():
    f = compute_fitness({"total": 0, "success": 0, "audited": 0, "audit_pass": 0,
                         "retries": 0, "cost": 0.0, "feedback": 0})
    assert f["samples"] == 0


def test_feedback_raises_score():
    base = {"total": 5, "success": 3, "audited": 5, "audit_pass": 3, "retries": 0, "cost": 0.0}
    low = compute_fitness({**base, "feedback": -3})
    high = compute_fitness({**base, "feedback": 3})
    assert high["score"] > low["score"]
