"""Plugin installer — pip/subprocess-based installation."""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


class PluginInstaller:
    """Install plugin dependencies."""

    @staticmethod
    async def install_pip(packages: list[str]) -> bool:
        """Install Python packages via pip."""
        if not packages:
            return True
        try:
            result = subprocess.run(
                ["pip", "install", *packages],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                logger.error("pip install failed: %s", result.stderr)
                return False
            return True
        except Exception:
            logger.exception("Failed to install packages: %s", packages)
            return False

    @staticmethod
    def verify_sha256(filepath: str, expected: str) -> bool:
        """Verify file SHA-256 checksum."""
        import hashlib
        from pathlib import Path

        if not expected:
            return True

        path = Path(filepath)
        if not path.exists():
            return False

        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        return sha == expected
