"""DuckDuckGoSearchEngine 解析单测：固定 HTML fixture。"""

from __future__ import annotations

import pytest

from tianshu.tools.hongluisi.engines.duckduckgo_search import _parse, _unwrap_ddg

_FIXTURE = """
<html><body>
<div class="result results_links">
  <div class="result__body">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">First Result</a>
    <a class="result__snippet">Snippet for first result.</a>
  </div>
</div>
<div class="result results_links">
  <div class="result__body">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fb">Second Result</a>
    <a class="result__snippet">Snippet for second result.</a>
  </div>
</div>
</body></html>
"""


@pytest.mark.unit
def test_unwrap_ddg_redirect():
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage"
    assert _unwrap_ddg(href) == "https://example.com/page"


@pytest.mark.unit
def test_unwrap_ddg_direct_url_passthrough():
    assert _unwrap_ddg("https://direct.example.com") == "https://direct.example.com"


@pytest.mark.unit
def test_unwrap_ddg_empty():
    assert _unwrap_ddg("") == ""


@pytest.mark.unit
def test_parse_extracts_results():
    results = _parse(_FIXTURE)
    assert len(results) == 2
    assert results[0].title == "First Result"
    assert results[0].url == "https://example.com/a"
    assert results[0].snippet == "Snippet for first result."
    assert results[1].url == "https://example.org/b"


@pytest.mark.unit
def test_parse_empty_html():
    assert _parse("") == []


@pytest.mark.unit
def test_parse_malformed_html_no_results():
    result = _parse("<not real html")
    assert isinstance(result, list)
