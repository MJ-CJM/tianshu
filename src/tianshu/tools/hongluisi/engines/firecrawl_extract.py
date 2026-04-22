"""Firecrawl /v1/extract engine。Spec Section 6。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from tianshu.tools.hongluisi.http_client import SharedHttpClient
from tianshu.tools.hongluisi.ssrf_guard import SSRFViolation, validate_url

logger = logging.getLogger(__name__)

FIRECRAWL_EXTRACT_URL = "https://api.firecrawl.dev/v1/extract"


@dataclass(frozen=True)
class ExtractOutcome:
    data: dict | None
    status: str           # "ok" | "error"
    reason: str | None
    http_status: int | None


class FirecrawlExtractEngine:
    name = "firecrawl_extract"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def extract(
        self, url: str, schema: dict, prompt: str | None = None
    ) -> ExtractOutcome:
        try:
            clean_url = await validate_url(url)
        except SSRFViolation as v:
            return ExtractOutcome(None, "error", v.code, None)

        payload: dict[str, Any] = {"urls": [clean_url], "schema": schema}
        if prompt:
            payload["prompt"] = prompt

        client = SharedHttpClient.instance()
        try:
            resp = await client._client.post(
                FIRECRAWL_EXTRACT_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.TimeoutException:
            return ExtractOutcome(None, "error", "timeout", None)
        except httpx.HTTPError as e:
            return ExtractOutcome(None, "error", f"http_error:{type(e).__name__}", None)

        if resp.status_code >= 400:
            return ExtractOutcome(
                None, "error", f"http_status:{resp.status_code}", resp.status_code
            )
        body = resp.json()
        if not body.get("success"):
            return ExtractOutcome(
                None, "error", "firecrawl_unsuccess", resp.status_code
            )
        return ExtractOutcome(body.get("data"), "ok", None, resp.status_code)


def build_firecrawl_extract() -> FirecrawlExtractEngine | None:
    key = os.getenv("TIANSHU_FIRECRAWL_API_KEY")
    if not key:
        return None
    return FirecrawlExtractEngine(api_key=key)
