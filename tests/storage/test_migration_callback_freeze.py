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
