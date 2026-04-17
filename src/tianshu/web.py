"""Conditional static file serving for container-integrated deployment."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles


def mount_web(app: FastAPI, static_dir: str = "/app/static") -> bool:
    """Mount frontend static files. Skip if directory doesn't exist. Return success."""
    static_path = Path(static_dir).resolve()
    if not static_path.is_dir() or not (static_path / "index.html").exists():
        return False

    assets_dir = static_path / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    index_html = str(static_path / "index.html")
    no_cache_headers = {"Cache-Control": "no-cache, no-store, must-revalidate"}

    @app.get("/{path:path}")
    async def spa_fallback(path: str) -> Response:
        """Serve static files with SPA fallback. API routes registered earlier take priority."""
        file = (static_path / path).resolve()
        # Path traversal protection
        try:
            file.relative_to(static_path)
        except ValueError:
            return Response(status_code=403)
        if file.is_file():
            return FileResponse(str(file))
        return FileResponse(index_html, headers=no_cache_headers)

    return True
