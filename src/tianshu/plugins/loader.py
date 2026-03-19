"""Plugin loader — discover and load plugins from directory."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from tianshu.plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)


class PluginLoader:
    """Discover and load plugins from a directory."""

    def __init__(self, plugins_dir: Path) -> None:
        self._plugins_dir = plugins_dir

    def discover(self) -> list[PluginManifest]:
        """Scan plugins directory for manifest files."""
        manifests: list[PluginManifest] = []
        if not self._plugins_dir.is_dir():
            return manifests

        for entry in sorted(self._plugins_dir.iterdir()):
            if not entry.is_dir():
                continue
            manifest_file = entry / "manifest.json"
            if not manifest_file.exists():
                continue

            try:
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
                manifest = PluginManifest(**data)
                manifests.append(manifest)
            except Exception:
                logger.warning("Failed to load plugin manifest: %s", manifest_file)

        return manifests

    def load_manifest(self, name: str) -> PluginManifest | None:
        """Load a specific plugin's manifest."""
        manifest_file = self._plugins_dir / name / "manifest.json"
        if not manifest_file.exists():
            return None
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            return PluginManifest(**data)
        except Exception:
            logger.warning("Failed to load plugin manifest: %s", name)
            return None
