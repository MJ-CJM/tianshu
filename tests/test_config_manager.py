"""Tests for ConfigManager."""

import pytest

from tianshu.config_manager import ConfigManager, LLMConfigState


class TestConfigManager:
    @pytest.fixture
    def cm(self):
        initial = LLMConfigState(name="default", model="gpt-4o", api_key="sk-test")
        return ConfigManager(initial)

    def test_initial_state(self, cm):
        assert cm.state.name == "default"
        assert cm.state.model == "gpt-4o"

    def test_update(self, cm):
        cm.update(temperature=0.5)
        assert cm.state.temperature == 0.5
        # Original values preserved
        assert cm.state.model == "gpt-4o"

    def test_immutability(self, cm):
        state = cm.state
        cm.update(temperature=0.9)
        # Original state unchanged
        assert state.temperature == 0.7

    def test_add_config(self, cm):
        new = LLMConfigState(name="claude", model="claude-3", api_key="sk-new")
        cm.add_config(new)
        configs, active = cm.list_configs()
        assert len(configs) == 2
        assert active == "default"

    def test_add_duplicate(self, cm):
        dup = LLMConfigState(name="default", model="x", api_key="y")
        with pytest.raises(ValueError, match="already exists"):
            cm.add_config(dup)

    def test_set_active(self, cm):
        new = LLMConfigState(name="alt", model="alt-model", api_key="k")
        cm.add_config(new)
        cm.set_active("alt")
        assert cm.state.model == "alt-model"

    def test_set_active_nonexistent(self, cm):
        with pytest.raises(KeyError):
            cm.set_active("nonexistent")

    def test_delete_config(self, cm):
        new = LLMConfigState(name="extra", model="x", api_key="k")
        cm.add_config(new)
        cm.delete_config("extra")
        configs, _ = cm.list_configs()
        assert len(configs) == 1

    def test_delete_active_fails(self, cm):
        with pytest.raises(ValueError, match="active"):
            cm.delete_config("default")

    def test_delete_last_fails(self, cm):
        with pytest.raises(ValueError, match="active"):
            cm.delete_config("default")

    def test_mask_api_key(self):
        assert ConfigManager.mask_api_key("") == "****"
        assert ConfigManager.mask_api_key("short") == "****"
        assert ConfigManager.mask_api_key("sk-1234567890abcdef") == "sk-1****cdef"

    def test_agent_config(self, cm):
        cfg = cm.agent_config
        assert cfg.agent_max_iterations == 20
        assert cfg.agent_timeout_seconds == 300

    def test_update_agent_config(self, cm):
        new = cm.update_agent_config(agent_max_iterations=50)
        assert new.agent_max_iterations == 50
        assert cm.agent_config.agent_max_iterations == 50
