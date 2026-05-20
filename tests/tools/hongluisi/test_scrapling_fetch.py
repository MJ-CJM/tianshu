"""ScraplingFetchEngine 单测：mock Scrapling Response，验证 outcome 映射。"""

from __future__ import annotations

import pytest

from tianshu.tools.hongluisi.engines.scrapling_fetch import (
    MODE_TO_NAME,
    ScraplingFetchEngine,
    build_scrapling,
)


class _FakePage:
    def __init__(self, status: int, body: bytes, url: str = "https://example.com"):
        self.status = status
        self.body = body
        self.encoding = "utf-8"
        self.url = url


@pytest.mark.unit
def test_mode_to_name_mapping():
    assert MODE_TO_NAME == {
        "http": "scrapling",
        "dynamic": "scrapling_dynamic",
        "stealthy": "scrapling_stealthy",
    }


@pytest.mark.unit
def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        ScraplingFetchEngine("bogus")


@pytest.mark.unit
async def test_fetch_ok(monkeypatch):
    engine = ScraplingFetchEngine("http")
    # Provide enough body text so trafilatura extracts ≥500 chars (MIN_EXTRACTED_LEN)
    article_text = "Hello world content here. " * 30  # ~780 chars
    html = (
        b"<html><body><article>"
        + article_text.encode()
        + b"</article></body></html>"
    )

    async def fake_invoke(url):
        return _FakePage(200, html)

    monkeypatch.setattr(engine, "_invoke", fake_invoke)
    outcome = await engine.fetch("https://example.com")
    assert outcome.status == "ok"
    assert outcome.http_status == 200
    assert "Hello world" in outcome.content


@pytest.mark.unit
async def test_fetch_http_error(monkeypatch):
    engine = ScraplingFetchEngine("http")

    async def fake_invoke(url):
        return _FakePage(404, b"")

    monkeypatch.setattr(engine, "_invoke", fake_invoke)
    outcome = await engine.fetch("https://example.com")
    assert outcome.status == "error"
    assert outcome.reason == "http_status:404"


@pytest.mark.unit
async def test_fetch_exception_wrapped(monkeypatch):
    engine = ScraplingFetchEngine("http")

    async def fake_invoke(url):
        raise RuntimeError("boom")

    monkeypatch.setattr(engine, "_invoke", fake_invoke)
    outcome = await engine.fetch("https://example.com")
    assert outcome.status == "error"
    assert outcome.reason == "scrapling_error:RuntimeError"


@pytest.mark.unit
async def test_fetch_ssrf_rejected():
    engine = ScraplingFetchEngine("http")
    outcome = await engine.fetch("http://169.254.169.254/latest/meta-data/")
    assert outcome.status == "error"


@pytest.mark.unit
def test_build_scrapling_returns_engine_or_none():
    engine = build_scrapling("http")
    assert engine is None or engine.name == "scrapling"
