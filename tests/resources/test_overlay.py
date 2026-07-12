"""PackagedDefaults 常驻视图与 COURT overlay 语义的单元面。"""

from pathlib import Path

from tianshu.resources import overlay
from tianshu.resources.overlay import (
    PackagedDefaults,
    court_override_path,
    packaged_defaults,
    reset_court_override,
    resolve_court_read,
)


def test_packaged_defaults_exposes_all_resource_dirs() -> None:
    defaults = PackagedDefaults()
    try:
        personas = defaults.personas_dir()
        assert (personas / "court" / "COURT.md").is_file()
        assert (personas / "bingbu" / "SOUL.md").is_file()
        templates = defaults.persona_templates_dir()
        assert (templates / "SOURCES.md").is_file()
        skills = defaults.builtin_skills_dir()
        assert (skills / "shell" / "SKILL.md").is_file()
        executor = defaults.executor_templates_dir()
        assert (executor / "edict" / "continuation.md").is_file()
        # 重复调用返回同一常驻路径（生命周期覆盖运行期）
        assert defaults.personas_dir() == personas
    finally:
        defaults.close()


def test_shared_singleton_is_stable() -> None:
    a = packaged_defaults()
    assert packaged_defaults() is a


def test_court_read_prefers_override_and_reset_is_idempotent(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime-personas"

    # 无 override：读取回退 packaged；reset 幂等且不产生任何文件
    packaged_court = resolve_court_read(runtime)
    assert packaged_court == packaged_defaults().personas_dir() / "court" / "COURT.md"
    assert reset_court_override(runtime) is False
    assert not court_override_path(runtime).exists()

    # 写 override 后读取优先 override
    override = court_override_path(runtime)
    override.parent.mkdir(parents=True)
    override.write_text("# my court", encoding="utf-8")
    assert resolve_court_read(runtime) == override

    # reset 删除 override 并自然回退；再次 reset 幂等
    assert reset_court_override(runtime) is True
    assert not override.exists()
    assert resolve_court_read(runtime) == packaged_court
    assert reset_court_override(runtime) is False


def test_reset_never_copies_packaged_bytes_into_overlay(tmp_path: Path) -> None:
    runtime = tmp_path / "rp"
    reset_court_override(runtime)
    assert not runtime.exists(), "reset 不得在 overlay 创建任何内容"
    assert overlay.packaged_defaults().personas_dir().joinpath("court", "COURT.md").is_file()
