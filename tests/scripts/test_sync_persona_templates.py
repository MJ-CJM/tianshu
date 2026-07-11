"""Safe, process-free persona template vendoring."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from urllib.request import Request

import pytest

from scripts import sync_persona_templates as sync


class _Response(io.BytesIO):
    def __init__(self, data: bytes, url: str) -> None:
        super().__init__(data)
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _archive(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def test_sync_repo_uses_pinned_https_archive_and_records_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = "a" * 40
    archive = _archive(
        {
            "owner-repo-sha/engineering/Agent.md": b"# Agent\n",
            "owner-repo-sha/engineering/README.md": b"ignored\n",
            "owner-repo-sha/scripts/tool.py": b"ignored\n",
        }
    )

    def fake_open(request: Request, allowed_hosts: frozenset[str]) -> _Response:
        if request.full_url.endswith("/commits/HEAD"):
            assert allowed_hosts == frozenset({"api.github.com"})
            return _Response(
                json.dumps({"sha": commit}).encode(),
                "https://api.github.com/repos/owner/repo/commits/HEAD",
            )
        assert request.full_url == f"https://codeload.github.com/owner/repo/tar.gz/{commit}"
        assert allowed_hosts == frozenset({"codeload.github.com"})
        return _Response(archive, request.full_url)

    destination = tmp_path / "templates"
    monkeypatch.setattr(sync, "DEST_ROOT", destination)
    monkeypatch.setattr(sync, "_open_https", fake_open)

    categories, files, resolved = sync.sync_repo("en", "owner", "repo", tmp_path / "stage")

    assert (categories, files, resolved) == (1, 1, commit)
    assert (destination / "en" / "engineering" / "Agent.md").read_bytes() == b"# Agent\n"


def test_archive_materialization_rejects_traversal(tmp_path: Path) -> None:
    archive = _archive({"owner-repo-sha/../escape.md": b"escape"})

    with pytest.raises(ValueError, match="unsafe archive path"):
        sync._materialize_templates(archive, tmp_path / "destination")

    assert not (tmp_path / "escape.md").exists()


def test_commit_metadata_rejects_malformed_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_open(request: Request, allowed_hosts: frozenset[str]) -> _Response:
        return _Response(
            b'{"sha":"not-a-commit"}',
            "https://api.github.com/repos/owner/repo/commits/HEAD",
        )

    monkeypatch.setattr(sync, "_open_https", fake_open)

    with pytest.raises(ValueError, match="commit SHA"):
        sync._resolve_github_commit("owner", "repo")


def test_redirect_handler_rejects_untrusted_intermediate_host() -> None:
    handler = sync._AllowedRedirectHandler(frozenset({"api.github.com"}))

    with pytest.raises(ValueError, match="untrusted archive endpoint"):
        handler.redirect_request(
            Request("https://api.github.com/repos/owner/repo/commits/HEAD"),
            None,
            302,
            "Found",
            {},
            "https://metadata.internal/then-back-to-github",
        )


@pytest.mark.parametrize(
    "url",
    (
        "http://api.github.com/repos/owner/repo",
        "https://user@api.github.com/repos/owner/repo",
        "https://api.github.com:444/repos/owner/repo",
        "https://api.github.com:invalid/repos/owner/repo",
    ),
)
def test_https_endpoint_rejects_downgrade_userinfo_and_nonstandard_port(url: str) -> None:
    with pytest.raises(ValueError, match="untrusted archive endpoint"):
        sync._validate_https_endpoint(url, frozenset({"api.github.com"}))
