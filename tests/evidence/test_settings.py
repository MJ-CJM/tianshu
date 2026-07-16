from __future__ import annotations

import pytest
from pydantic import ValidationError

from tianshu.config import TianshuSettings


def test_artifact_settings_are_bounded_and_consistent() -> None:
    settings = TianshuSettings(_env_file=None)

    assert settings.artifact_dir == "~/.tianshu/artifacts"
    assert settings.artifact_max_bytes == 100 * 1024 * 1024
    assert settings.artifact_quota_bytes == 5 * 1024 * 1024 * 1024

    for values in (
        {"artifact_max_bytes": True},
        {"artifact_max_bytes": 0},
        {"artifact_quota_bytes": 4, "artifact_max_bytes": 8},
    ):
        with pytest.raises(ValidationError, match="artifact"):
            TianshuSettings(_env_file=None, **values)
