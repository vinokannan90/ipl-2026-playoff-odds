"""News/injury scout — DISABLED by default.

Real implementation requires a vetted, licensed news source (RSS or paid API).
Until then this is a stub that returns an empty result so we never inject
hallucinated news into priors.
"""

from __future__ import annotations

from typing import Any

from iplodds.config import get_settings


async def fetch_signals(team_code: str | None = None) -> dict[str, Any]:
    s = get_settings()
    if not s.feature_scout:
        return {
            "enabled": False,
            "signals": [],
            "note": "Scout disabled. Enable IPLODDS_FEATURE_SCOUT after wiring a vetted news source.",
        }
    return {"enabled": True, "signals": [], "note": "Scout enabled but no source configured yet."}
