"""冻结迁移 callback 的源码指纹守卫。

migration checksum 只覆盖 SQL 语句文本（``migrations.py`` 的 ``_*_CHECKSUM``），
``upgrade``/validate callback 的 Python 源码不在其中——改写 callback 不会触发
ledger 漂移告警（G1.4b3 期间 V4 callback 曾被无声改写，此缺口已被踩中）。

本测试把已提交迁移的 callback 与其行为等价关键 helper 的源码指纹显式冻结：
任何改写都会在此现形。若属经裁决的正式修订，必须先在
``docs/cc-fable-v1/PROGRESS.md`` 记录裁决与理由，再同步更新指纹。
"""

import hashlib
import inspect
import textwrap
from collections.abc import Callable

import pytest

from tianshu.secrets import vault as vault_module
from tianshu.storage import _base as storage_base_module
from tianshu.storage import migration_ledger as migration_ledger_module
from tianshu.storage import migrations as migrations_module
from tianshu.storage.migrations import MIGRATIONS

_FROZEN_UPGRADE_FINGERPRINTS: dict[str, str] = {
    "0001_adopt_v042_baseline": (
        "488dec454e3ad9fa5963ddf6617b8fa00729d3778e6dd7b3db46e2f653efdc4a"
    ),
    "0002_auth_tokens": ("dc9d3820bfeb9a7063d361f0559bb71299135b24532ac6ee6574e980c59d6a04"),
    "0003_governance_contracts": (
        "155be387fe422f33698f0b8890dbf021e61d56abfc080a928620e4ae9a5f5747"
    ),
    "0004_workspace_foundation": (
        "a2b2f236b744d521bef6879945e4afccc61b47cc549cbfbd475dd0e5cbf3d515"
    ),
    "0005_governed_apply_bindings": (
        "6e9f9594dd9dfbe51185d050a4ec93b1881399067af03e28fe97bc742e3d91a2"
    ),
    # v6 于 S1.2 追加（G1.5 六默认部门 seed），随切片提交冻结。
    "0006_seed_default_personas": (
        "5951a0b2025bf25608378ce6914afeeaf699fc661c1b4c667bc45af54830c132"
    ),
    # v7 于 S2 追加不可变 SystemAudit 表、索引与拒绝替换/更新/删除的触发器。
    "0007_system_audit_events": (
        "d46142290fbe10412291c6f0d3b73d6c83835c0a0247cb43bcd599d504afb070"
    ),
    "0008_encrypt_mcp_secret_mappings": (
        "c0576edfc0637532b1488aae740e658f122e837b2155648f0dabf351671bd3a5"
    ),
    # v9 于 S3 追加 Edict 幂等提交与 durable outbox 基础表。
    "0009_durable_edict_ingress": (
        "70c0dd79302fd10401c7dcacfb675eb047f759650cbc9ceb657f54c5b27b1588"
    ),
    # v10 将 Telegram 去重身份修正为 (instance_id, update_id)。
    "0010_telegram_seen_instance_identity": (
        "ae200078d7b60187fdb8c15a75082b9a9e655cb955c77b12f7512a77f5a36daa"
    ),
    # v11 追加 durable Decision/RunState 表与不可变 resolution 触发器。
    "0011_decisions_run_state": (
        "bda8e1761d17d27051e5c7b52dbfa224398e86c794666007f61f8e2a097d5e7c"
    ),
    # v12 adds validation and no-replace guards without changing frozen v11.
    "0012_decision_run_state_guards": (
        "5f554ac8d24fe9ea3f060c81365100ae4ecb40d55340421cb18882268155b9c8"
    ),
    # v13 binds new apply projections to resolved generic governed-apply decisions.
    "0013_governed_apply_decision_binding": (
        "2608a3bcc6f66c6a68fb745f4aa058fc65b130153b71a25aa870bcb280ce6f9c"
    ),
    # v14 adds the fenced execution-attempt ledger without rewriting v1-v13.
    "0014_execution_attempt_ledger": (
        "55bc5dcb3a5d04df526dfcf92e59b9e41f97c878b345e00acf7f4dfdc33cfee2"
    ),
    # v15 adds the managed side-effect intent/receipt journal after the live v14 tail.
    "0015_side_effect_journal": (
        "db69496a047a2af8a248b04db84052aa38f48cd9e407bb430384ded15fd52b1b"
    ),
    # v16 adds immutable evidence metadata after the live v15 tail.
    "0016_artifacts_evidence": ("b999d1ca8e809321c91eaab5ab6a966a4902bde1a213def9afe0453119ece233"),
    # v17 adds retained internal delivery and root correlation after the live v16 tail.
    "0017_internal_notification_delivery": (
        "dbd30a6b7a981abc1d9c5f071a2e81276ad87f4ffa38f43f388ce8f2017450fb"
    ),
    # v18 appends governed candidate, gate, lifecycle, promotion, routing and assignment state.
    "0018_governed_evolution_candidates": (
        "b064609eb8d0ba0b5dc7d50d43512a0f3aa56bff1d1badcb7f861d650b5bef42"
    ),
    # v19 建 model_providers 表并按 profile 匹配回填 llm_configs.provider_id（统一模型注册表）。
    "0019_model_providers": ("719bc4cf0b2543d88294bf1a23f1ff41621e1c5398b9f9ea4c586406f2316cc0"),
    # v20 把 llm_configs 明文 api_key 加密入 network_credentials(kind='llm_provider')
    # 后经临时表重建物理删除明文列（照抄 v8 secure_delete 模式，登记为 sensitive migration）。
    "0020_encrypt_llm_config_keys": (
        "dea4ecedab5a639ee4e4449807f96981670e56528480ae5a409f84e5c7bbea31"
    ),
    # v21 追加 app_settings KV 表（agent_config/任务槽位持久化）。
    "0021_app_settings": ("cb71789ef38e922fee424f7cbfe458f82ae59543eb6087f711c64b2f05e1efa0"),
    # v22 重建 run_evolution_assignments 的 no_delete 触发器：仅保护真实验记录
    # （candidate_id 非空），legacy 冠军占位可随敕令清理。
    "0022_legacy_assignment_cleanup": (
        "01b7aa95582bc808f20c0e715ce3ecc43e6081218a2ec2900bcbe7c0f462f31f"
    ),
    # v23 retains cache-read usage in the canonical cost ledger.
    "0023_cost_cache_read_tokens": (
        "c9c8c0f91bdb539abfe41263300fdb3a6bdfe87e9da83fa8c4816330fa424a3c"
    ),
    # v24 retains per-channel acceptance so retries skip channels already accepted.
    "0024_notification_channel_progress": (
        "ddedca050950e01e662e1f953a85bb988b61e8822d8d59398dd348e26d58fdc2"
    ),
    # v25 gives each official an explicit out-of-workspace path allowlist (issue #35).
    "0025_persona_allowed_paths": (
        "db9bfea91a63fa9ba8da480bcacc330c23eb72f95632060bd4852acf286ab308"
    ),
    # v26 把存量占位 tool_tier_max=0 提到 4：官员工具 ACL 自 #40 起被强制，
    # 提档保持既有官员实际行为不变；非 0 的有意声明原样保留并自此生效。
    "0026_persona_tier_enforcement": (
        "da1d14e572b00e34367cb062868c5d2d65fdba1e942c3b1d5a524e7b99a3a1d9"
    ),
    # v27 gives each official an optional dedicated workspace root (issue #33).
    "0027_persona_workspace_dir": (
        "c2124b4345989f1c04cb6a0ec274bf09637c37e83beff6df9bbbdd36d591b0fa"
    ),
}

_FROZEN_HELPER_FINGERPRINTS: dict[tuple[str, str], str] = {
    ("migrations", "_validate_governance_contract_schema"): (
        "1f110e370dd643986f8fb2bff56c0254eff36bbbd894b74e9b0b9a7e3af74d85"
    ),
    ("migrations", "_validate_workspace_foundation_schema"): (
        "836132c67e3b8d3b3d564bee7b9b966f7dd650f69db98a90b808804bc9a61e35"
    ),
    # canonical adopt 门控：决定"整体跳过全部 upgrade callback"的函数，
    # 放宽其比较等同于绕过所有迁移逻辑，必须与 callback 同级冻结。
    ("migrations", "_matches_canonical_schema"): (
        "776fc68ea1b024321a9b9a351fcea2bd20e43417ce224d8f9909a9c5766afe9c"
    ),
    ("migration_ledger", "adopt_migrations"): (
        "74022265c8b0a3b47301ab947fa3c1ed5dd5ad856fd2bb11d4c4e026704c3a01"
    ),
    ("migrations", "_parse_legacy_secret_mapping"): (
        "6c29b81b33339a2c85c85ac628e2196d1074d4d338e31c9c446fb89341918429"
    ),
    ("migrations", "_encrypt_verified_mapping"): (
        "6e70862523c3614fe3d701976e0b9240117fdf02d8ce41f9181d1d162d9ef291"
    ),
    ("storage_base", "_truncate_sensitive_migration_wal"): (
        "ed0fa42bcad3116fc412702e3da6b93d743352875d1a219d933e92529746b71c"
    ),
    ("vault", "encrypt_canonical_mapping"): (
        "7d44ac2228b6f041aaa5ece476301180b4d166c5732873b07fb3e75e4a6a9bc0"
    ),
    ("vault", "decrypt_canonical_mapping"): (
        "c752b957ea17b7936ccc2818b3356eb8e73ce8646cd429d0b6519f74cca09f09"
    ),
    ("vault", "require_mcp_vault"): (
        "0336ca958f13df4125d97ca2a3cc5b248ad0b571d17164e5548cbc767330f236"
    ),
}

_HELPER_MODULES = {
    "migrations": migrations_module,
    "migration_ledger": migration_ledger_module,
    "storage_base": storage_base_module,
    "vault": vault_module,
}

_FREEZE_VIOLATION_HINT = (
    "冻结迁移的 callback 源码被修改。已提交迁移的行为不得漂移；"
    "若这是经裁决的正式修订，先在 docs/cc-fable-v1/PROGRESS.md 记录后更新指纹。"
)

_V8_SECURITY_HELPERS = {
    ("migrations", "_parse_legacy_secret_mapping"),
    ("migrations", "_encrypt_verified_mapping"),
    ("vault", "encrypt_canonical_mapping"),
    ("vault", "decrypt_canonical_mapping"),
    ("vault", "require_mcp_vault"),
    ("storage_base", "_truncate_sensitive_migration_wal"),
}


def _source_fingerprint(obj: Callable[..., object]) -> str:
    source = textwrap.dedent(inspect.getsource(obj))
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def test_every_migration_upgrade_callback_is_fingerprinted() -> None:
    assert {migration.name for migration in MIGRATIONS} == set(_FROZEN_UPGRADE_FINGERPRINTS), (
        "MIGRATIONS 序列与冻结指纹表不同步：新增迁移必须同时登记其 callback 指纹"
    )


def test_v8_security_helpers_are_all_fingerprinted() -> None:
    assert _V8_SECURITY_HELPERS.issubset(_FROZEN_HELPER_FINGERPRINTS)


@pytest.mark.parametrize("migration", MIGRATIONS, ids=lambda m: m.name)
def test_migration_upgrade_callback_source_is_frozen(migration) -> None:  # type: ignore[no-untyped-def]
    actual = _source_fingerprint(migration.upgrade)
    assert actual == _FROZEN_UPGRADE_FINGERPRINTS[migration.name], (
        f"{migration.name} 的 upgrade callback "
        f"{migration.upgrade.__name__} 指纹漂移。{_FREEZE_VIOLATION_HINT}"
    )


@pytest.mark.parametrize(
    "helper_key", sorted(_FROZEN_HELPER_FINGERPRINTS), ids=lambda key: f"{key[0]}.{key[1]}"
)
def test_migration_validate_helper_source_is_frozen(helper_key: tuple[str, str]) -> None:
    module_name, helper_name = helper_key
    helper = getattr(_HELPER_MODULES[module_name], helper_name)
    actual = _source_fingerprint(helper)
    assert actual == _FROZEN_HELPER_FINGERPRINTS[helper_key], (
        f"迁移采纳判定 helper {module_name}.{helper_name} 指纹漂移。{_FREEZE_VIOLATION_HINT}"
    )
