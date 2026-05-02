"""Match-priors agent.

Asks the LLM to assign a per-match home-team win probability based ONLY on
the standings + remaining schedule we provide (no scraped news yet). The
output schema is strict JSON: {matchId: {pHome: 0..1, rationale: str}}.

Honesty guardrails baked into the prompt:
- Probabilities must reflect reasoning over the supplied data, not invented news.
- Must stay within [0.10, 0.90] unless one team is mathematically eliminated.
- Rationale must be <= 200 chars.
"""

from __future__ import annotations

import json
from typing import Any

import orjson
import structlog

from iplodds.config import get_settings
from iplodds.data import iplt20_client
from iplodds.data.cache import get_cache
from iplodds.llm import client as llm

log = structlog.get_logger(__name__)


SYSTEM = """You are an IPL analyst. For each remaining IPL 2026 match, output a
home-team win probability based ONLY on the standings and schedule provided.

Hard rules:
- Output STRICT JSON: {"priors": {"<matchId>": {"pHome": 0.0-1.0, "rationale": "<=200 chars"}}}
- Clamp every pHome to [0.10, 0.90].
- Rationale must cite observable facts (current points, NRR, head-to-head, fixtures left).
- DO NOT invent injuries, lineups, weather, or news. If unsure, return 0.5.
- Keep rationales terse and factual."""


def _build_user_payload(standings_raw: dict, schedule_raw: dict) -> dict[str, Any]:
    teams = []
    by_id = {}
    for t in standings_raw.get("points", []):
        teams.append(
            {
                "id": str(t.get("TeamID")),
                "code": t.get("TeamCode"),
                "name": t.get("TeamName"),
                "pts": int(t.get("Points") or 0),
                "w": int(t.get("Wins") or 0),
                "l": int(t.get("Loss") or 0),
                "nr": int(t.get("NoResult") or 0),
                "nrr": float(t.get("NetRunRate") or 0),
            }
        )
        by_id[str(t.get("TeamID"))] = t.get("TeamCode")

    remaining = []
    for m in schedule_raw.get("Matchsummary", []):
        status = (m.get("MatchStatus") or "").lower()
        if status in {"post", "completed", "result"}:
            continue
        home = str(m.get("HomeTeamID") or "")
        away = str(m.get("AwayTeamID") or "")
        if not home or not away:
            continue
        remaining.append(
            {
                "matchId": str(m.get("MatchID")),
                "date": m.get("MatchDate"),
                "home": by_id.get(home, home),
                "away": by_id.get(away, away),
                "homeId": home,
                "awayId": away,
            }
        )

    return {"teams": teams, "remaining": remaining}


async def compute_priors(force: bool = False) -> dict[str, Any]:
    s = get_settings()
    cache = get_cache()
    key = f"priors-{s.iplt20_competition_id}.json"
    if not force:
        cached = await cache.get(key, s.cache_ttl_priors_s)
        if cached is not None:
            return cached

    standings = await iplt20_client.get_standings()
    schedule = await iplt20_client.get_schedule()
    payload = _build_user_payload(standings, schedule)

    if not payload["remaining"]:
        result = {"priors": {}, "generated_at": None, "model": llm.model_name()}
        await cache.set(key, result)
        return result

    try:
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=2000,
        )
        content = resp.choices[0].message.content or "{}"
        parsed = orjson.loads(content)
        priors = parsed.get("priors", {})
        # Sanitize: clamp + drop unknown keys
        valid_ids = {m["matchId"] for m in payload["remaining"]}
        clean: dict[str, dict[str, Any]] = {}
        for mid, v in priors.items():
            if mid not in valid_ids or not isinstance(v, dict):
                continue
            try:
                p = float(v.get("pHome", 0.5))
            except (TypeError, ValueError):
                p = 0.5
            p = max(0.10, min(0.90, p))
            r = str(v.get("rationale", ""))[:200]
            clean[mid] = {"pHome": p, "rationale": r}
        result = {"priors": clean, "model": llm.model_name()}
        await cache.set(key, result)
        return result
    except Exception:
        log.exception("priors.compute_failed")
        return {"priors": {}, "error": "llm_unavailable", "model": llm.model_name()}
