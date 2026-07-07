"""HTTP client wrapper for CLI commands."""

from __future__ import annotations

import os
import sys

import httpx


def _base_url() -> str:
    return os.environ.get("TIANSHU_API_URL", "http://localhost:8000")


def _request(method: str, path: str, **kwargs) -> dict:
    try:
        with httpx.Client(base_url=_base_url(), timeout=360.0) as client:
            resp = client.request(method, path, **kwargs)
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        print(f"Cannot connect to Tianshu ({_base_url()})", file=sys.stderr)
        raise SystemExit(1) from None
    except httpx.HTTPStatusError as e:
        print(f"API error: {e.response.status_code} - {e.response.text}", file=sys.stderr)
        raise SystemExit(1) from e


def api_get(path: str, params: dict | None = None) -> dict:
    return _request("GET", path, params=params)


def api_post(path: str, json_data: dict) -> dict:
    return _request("POST", path, json=json_data)


def api_put(path: str, json_data: dict) -> dict:
    return _request("PUT", path, json=json_data)


def api_delete(path: str) -> dict:
    return _request("DELETE", path)


def get_client() -> httpx.Client:
    """Get a reusable HTTP client for CLI commands."""
    return httpx.Client(base_url=_base_url(), timeout=360.0)
