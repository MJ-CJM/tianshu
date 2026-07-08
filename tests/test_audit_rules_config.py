"""测试 auditor.rules_config —— YAML 审计规则加载器(失败安全)。"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from tianshu.auditor.rules_config import AuditRulesConfig, load_audit_rules

# 与 rules.py / reviewer.py 现状一致的默认基线,用于断言「行为不变」。
_DEFAULTS = AuditRulesConfig()


def _write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "audit_rules.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# --- 无文件 / None → 全默认 ---------------------------------------------------


def test_none_path_returns_defaults() -> None:
    assert load_audit_rules(None) == _DEFAULTS


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    assert load_audit_rules(tmp_path / "does_not_exist.yaml") == _DEFAULTS


def test_directory_path_returns_defaults(tmp_path: Path) -> None:
    # 路径指向目录(非文件)→ 回退默认,不抛。
    assert load_audit_rules(tmp_path) == _DEFAULTS


def test_empty_file_returns_defaults(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "")
    assert load_audit_rules(path) == _DEFAULTS


def test_defaults_match_current_behavior() -> None:
    # 默认值必须与 auditor 现状硬编码一致,保证不加载 YAML 时行为不变。
    assert _DEFAULTS.check_token_budget is True
    assert _DEFAULTS.check_execution_error is True
    assert _DEFAULTS.check_empty_result is True
    assert _DEFAULTS.review_temperature == 0.1
    assert _DEFAULTS.review_max_tokens == 512
    assert _DEFAULTS.risk_keywords == ()


# --- 合法 YAML 覆盖部分键 → 生效,其余保持默认 -------------------------------


def test_valid_partial_override(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        check_token_budget: false
        review_temperature: 0.5
        review_max_tokens: 256
        risk_keywords:
          - "rm -rf"
          - "DROP TABLE"
        """,
    )
    cfg = load_audit_rules(path)

    # 覆盖生效
    assert cfg.check_token_budget is False
    assert cfg.review_temperature == 0.5
    assert cfg.review_max_tokens == 256
    assert cfg.risk_keywords == ("rm -rf", "DROP TABLE")
    # 未提供的键保持默认
    assert cfg.check_execution_error is True
    assert cfg.check_empty_result is True


def test_range_boundaries_accepted(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        review_temperature: 1.0
        review_max_tokens: 1
        """,
    )
    cfg = load_audit_rules(path)
    assert cfg.review_temperature == 1.0
    assert cfg.review_max_tokens == 1


def test_int_temperature_coerced_to_float(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "review_temperature: 0\n")
    cfg = load_audit_rules(path)
    assert cfg.review_temperature == 0.0


# --- 坏 YAML(未知键 / 类型错 / 越界)→ 回退默认,不抛 -----------------------


def test_unknown_key_ignored(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        check_empty_result: false
        totally_unknown_key: 123
        """,
    )
    cfg = load_audit_rules(path)
    assert cfg.check_empty_result is False  # 已知键仍生效
    assert not hasattr(cfg, "totally_unknown_key")


def test_wrong_types_fall_back_to_defaults(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        check_token_budget: "yes"       # 非真 bool
        review_temperature: "hot"       # 非数值
        review_max_tokens: true         # bool 不算 int
        risk_keywords: "not a list"     # 非列表
        """,
    )
    cfg = load_audit_rules(path)
    assert cfg == _DEFAULTS


def test_out_of_range_values_fall_back(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        review_temperature: 5.0     # > 1.0
        review_max_tokens: 0        # < 1
        """,
    )
    cfg = load_audit_rules(path)
    assert cfg.review_temperature == _DEFAULTS.review_temperature
    assert cfg.review_max_tokens == _DEFAULTS.review_max_tokens


def test_max_tokens_above_range_falls_back(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "review_max_tokens: 999999\n")
    cfg = load_audit_rules(path)
    assert cfg.review_max_tokens == _DEFAULTS.review_max_tokens


def test_non_string_keyword_items_dropped(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        """
        risk_keywords:
          - "sudo"
          - 42
          - null
          - "curl"
        """,
    )
    cfg = load_audit_rules(path)
    assert cfg.risk_keywords == ("sudo", "curl")


def test_malformed_yaml_falls_back(tmp_path: Path) -> None:
    # 语法错误(未闭合的流式序列)→ 回退默认,不抛。
    path = _write_yaml(tmp_path, "review_max_tokens: [1, 2\n")
    assert load_audit_rules(path) == _DEFAULTS


def test_non_mapping_top_level_falls_back(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "- a\n- b\n")
    assert load_audit_rules(path) == _DEFAULTS


# --- 结构性质:immutable + to_dict --------------------------------------------


def test_config_is_frozen() -> None:
    cfg = AuditRulesConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.check_empty_result = False  # type: ignore[misc]


def test_to_dict_shape_and_json_friendly() -> None:
    cfg = AuditRulesConfig(risk_keywords=("a", "b"))
    assert cfg.to_dict() == {
        "check_token_budget": True,
        "check_execution_error": True,
        "check_empty_result": True,
        "review_temperature": 0.1,
        "review_max_tokens": 512,
        "risk_keywords": ["a", "b"],  # tuple 转 list
    }


def test_str_path_accepted(tmp_path: Path) -> None:
    # path 支持 str 而非仅 Path。
    path = _write_yaml(tmp_path, "check_token_budget: false\n")
    cfg = load_audit_rules(str(path))
    assert cfg.check_token_budget is False
