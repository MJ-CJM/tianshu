"""HTTP client wrapper and secure session lifecycle for CLI commands."""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

try:
    import fcntl
except ImportError:  # pragma: no cover - Tianshu CLI targets Unix-like hosts.
    fcntl = None  # type: ignore[assignment]

_CREDENTIAL_VERSION = 1


def _base_url() -> str:
    return os.environ.get("TIANSHU_API_URL", "http://localhost:8000").rstrip("/")


def _credential_path() -> Path:
    configured = os.environ.get("TIANSHU_CREDENTIAL_FILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path("~/.tianshu/credentials.json").expanduser()


def require_secure_api_transport(api_url: str | None = None) -> str:
    """Allow credentials only over HTTPS, except for an explicit loopback HTTP URL."""
    resolved = (api_url or _base_url()).rstrip("/")
    parsed = urlsplit(resolved)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("credential API URLs cannot contain credentials, query, or fragment")
    if parsed.scheme == "https" and parsed.hostname:
        return resolved
    if parsed.scheme == "http" and parsed.hostname:
        if parsed.hostname.lower() == "localhost":
            return resolved
        try:
            if ipaddress.ip_address(parsed.hostname).is_loopback:
                return resolved
        except ValueError:
            pass
    raise ValueError("credentials require HTTPS unless the API URL is loopback")


@dataclass(frozen=True)
class SessionCredential:
    version: int
    api_url: str
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)


def _load_session_credential_file() -> SessionCredential | None:
    path = _credential_path()
    try:
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            return None
        if stat.S_IMODE(file_stat.st_mode) & 0o077:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if set(payload) != {"version", "api_url", "access_token", "refresh_token"}:
        return None
    if payload.get("version") != _CREDENTIAL_VERSION:
        return None
    if not all(
        isinstance(payload.get(name), str) and payload[name]
        for name in ("api_url", "access_token", "refresh_token")
    ):
        return None
    return SessionCredential(
        version=_CREDENTIAL_VERSION,
        api_url=payload["api_url"].rstrip("/"),
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
    )


def load_session_credential(*, api_url: str | None = None) -> SessionCredential | None:
    """Load a same-server credential only when its file is a private regular file."""
    credential = _load_session_credential_file()
    expected_url = (api_url or _base_url()).rstrip("/")
    if credential is None or credential.api_url != expected_url:
        return None
    return credential


def save_session_credential(credential: SessionCredential) -> None:
    """Atomically persist opaque session tokens with owner-only permissions."""
    path = _credential_path()
    parent_existed = path.parent.exists()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not parent_existed or not os.environ.get("TIANSHU_CREDENTIAL_FILE", "").strip():
        path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    file_descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": _CREDENTIAL_VERSION,
                    "api_url": credential.api_url.rstrip("/"),
                    "access_token": credential.access_token,
                    "refresh_token": credential.refresh_token,
                },
                handle,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def delete_session_credential(*, expected: SessionCredential | None = None) -> bool:
    path = _credential_path()
    try:
        if expected is not None:
            current = _load_session_credential_file()
            if current is not None and current != expected:
                return True
        path.unlink(missing_ok=True)
        return not path.exists()
    except OSError:
        return False


@contextmanager
def _credential_lock() -> Iterator[None]:
    credential_path = _credential_path()
    path = credential_path.with_suffix(credential_path.suffix + ".lock")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def extract_session_cookies(
    response: httpx.Response,
    *,
    api_url: str | None = None,
) -> SessionCredential | None:
    try:
        access_token = response.cookies.get("tianshu_access")
        refresh_token = response.cookies.get("tianshu_refresh")
    except httpx.CookieConflict:
        return None
    if not access_token or not refresh_token:
        return None
    return SessionCredential(
        version=_CREDENTIAL_VERSION,
        api_url=(api_url or _base_url()).rstrip("/"),
        access_token=access_token,
        refresh_token=refresh_token,
    )


def _env_pat() -> str | None:
    token = os.environ.get("TIANSHU_API_TOKEN", "").strip()
    return token or None


def auth_headers() -> dict[str, str]:
    """Build CLI transport headers without logging or placing tokens in URLs."""
    headers = {"X-Tianshu-Client": "cli"}
    token = _env_pat()
    if token is None:
        stored = load_session_credential()
        token = stored.access_token if stored is not None else None
    if token:
        require_secure_api_transport()
        headers["Authorization"] = f"Bearer {token}"
    return headers


class TianshuClient(httpx.Client):
    """HTTP client that rotates a stored session once after an access-token 401."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float = 360.0,
        credential: SessionCredential | None = None,
        ignore_env: bool = False,
    ) -> None:
        resolved_url = (base_url or _base_url()).rstrip("/")
        env_token = None if ignore_env else _env_pat()
        self._auth_source: str
        self._access_token: str | None
        self._credential: SessionCredential | None
        if env_token is not None:
            self._auth_source = "env"
            self._access_token = env_token
            self._credential = None
        else:
            stored = credential or load_session_credential(api_url=resolved_url)
            self._auth_source = "session" if stored is not None else "none"
            self._access_token = stored.access_token if stored is not None else None
            self._credential = stored
        if self._access_token is not None:
            require_secure_api_transport(resolved_url)
        self._api_url = resolved_url
        super().__init__(
            base_url=resolved_url,
            timeout=timeout,
            headers={"X-Tianshu-Client": "cli"},
        )

    @staticmethod
    def _path(url: str | httpx.URL) -> str:
        parsed = urlsplit(str(url))
        return parsed.path or str(url).split("?", 1)[0]

    def _authenticated_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        outgoing = dict(kwargs)
        headers = httpx.Headers(outgoing.get("headers"))
        if self._access_token and "authorization" not in headers:
            headers["Authorization"] = f"Bearer {self._access_token}"
        outgoing["headers"] = headers
        return outgoing

    def _refresh_session(self) -> bool:
        credential = self._credential
        if credential is None:
            return False
        with _credential_lock():
            latest = _load_session_credential_file()
            if latest is None:
                return False
            if latest != credential:
                if latest.api_url == self._api_url:
                    self._credential = latest
                    self._access_token = latest.access_token
                    return True
                return False
            response = super().request(
                "POST",
                "/api/auth/refresh",
                headers={
                    "X-Tianshu-Client": "cli",
                    "Cookie": f"tianshu_refresh={credential.refresh_token}",
                },
            )
            if response.status_code == 401:
                delete_session_credential(expected=credential)
                self._credential = None
                self._access_token = None
                self._auth_source = "none"
                return False
            if response.is_error:
                return False
            rotated = extract_session_cookies(response, api_url=self._api_url)
            if rotated is None:
                return False
            save_session_credential(rotated)
            self._credential = rotated
            self._access_token = rotated.access_token
            return True

    def request(self, method: str, url: str | httpx.URL, **kwargs: Any) -> httpx.Response:
        response = super().request(method, url, **self._authenticated_kwargs(kwargs))
        path = self._path(url)
        can_refresh = self._auth_source == "session" and path not in {
            "/api/auth/refresh",
        }
        if method.upper() == "POST" and path == "/api/auth/session":
            can_refresh = False
        if response.status_code == 401 and can_refresh and self._refresh_session():
            response = super().request(method, url, **self._authenticated_kwargs(kwargs))
        return response


def _request(method: str, path: str, **kwargs: Any) -> dict:
    try:
        with get_client() as client:
            resp = client.request(method, path, **kwargs)
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        print(f"Cannot connect to Tianshu ({_base_url()})", file=sys.stderr)
        raise SystemExit(1) from None
    except httpx.HTTPStatusError as error:
        print(
            f"API error: {error.response.status_code} - {error.response.text}",
            file=sys.stderr,
        )
        raise SystemExit(1) from error


def api_get(path: str, params: dict | None = None) -> dict:
    return _request("GET", path, params=params)


def api_post(
    path: str,
    json_data: dict,
    *,
    headers: dict[str, str] | None = None,
) -> dict:
    return _request("POST", path, json=json_data, headers=headers)


def api_put(path: str, json_data: dict) -> dict:
    return _request("PUT", path, json=json_data)


def api_delete(path: str) -> dict:
    return _request("DELETE", path)


def get_client() -> TianshuClient:
    """Get a reusable, credential-aware HTTP client for CLI commands."""
    return TianshuClient()
