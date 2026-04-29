"""Upstream IPL data client.

Fetches the JSONP feeds from scores.iplt20.com and strips the callback wrapper
to return clean JSON. Cached via the Cache layer.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from iplodds.config import get_settings
from iplodds.data.cache import get_cache

log = structlog.get_logger(__name__)

# Match `name(... json ...);?` allowing whitespace.
_JSONP_RE = re.compile(r"^\s*[A-Za-z_][\w]*\s*\(\s*(.+?)\s*\)\s*;?\s*$", re.DOTALL)


def _strip_jsonp(body: str) -> str:
    m = _JSONP_RE.match(body)
    if not m:
        # Some endpoints already return raw JSON
        return body
    return m.group(1)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    reraise=True,
)
async def _fetch(url: str) -> Any:
    s = get_settings()
    async with httpx.AsyncClient(
        timeout=s.upstream_timeout_s,
        headers={
            # iplt20.com fronts feeds behind a CDN that 403s non-browser UAs.
            # We mimic a real browser; the data is publicly served to the same UA on the website.
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.iplt20.com/",
            "Origin": "https://www.iplt20.com",
        },
        follow_redirects=True,
    ) as client:
        r = await client.get(url)
        r.raise_for_status()
        import orjson
        return orjson.loads(_strip_jsonp(r.text))


async def get_standings() -> dict[str, Any]:
    s = get_settings()
    cache = get_cache()
    key = f"standings-{s.iplt20_competition_id}.json"
    cached = await cache.get(key, s.cache_ttl_standings_s)
    if cached is not None:
        return cached
    url = f"{s.iplt20_base}/stats/{s.iplt20_competition_id}-groupstandings.js"
    data = await _fetch(url)
    await cache.set(key, data)
    return data


async def get_schedule() -> dict[str, Any]:
    s = get_settings()
    cache = get_cache()
    key = f"schedule-{s.iplt20_competition_id}.json"
    cached = await cache.get(key, s.cache_ttl_schedule_s)
    if cached is not None:
        return cached
    url = f"{s.iplt20_base}/{s.iplt20_competition_id}-matchschedule.js"
    data = await _fetch(url)
    await cache.set(key, data)
    return data
