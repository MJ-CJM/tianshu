from __future__ import annotations

import subprocess


def test_personal_ide_project_files_are_not_tracked() -> None:
    tracked = subprocess.check_output(["git", "ls-files", ".idea"], text=True).splitlines()
    assert tracked == []
