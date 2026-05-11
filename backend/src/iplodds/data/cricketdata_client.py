"""CricAPI (cricketdata.org) client for full match scorecards.

Free plan: 100 req/day. Cache aggressively to stay within limits.

Endpoints used:
  - GET /series?search=Indian+Premier+League+2026  → find IPL 2026 series GUID
  - GET /series_info?id=<series_id>               → full match list for the season
  - GET /currentMatches                           → currently live IPL matches
  - GET /match_scorecard?id=<match_id>            → full batting/bowling scorecard

Public API:
  - get_scorecard(match_hint)   → full batting/bowling scorecard for agents
  - get_latest_result()         → lightweight winner/loser/status for the badge display
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
import structlog

from iplodds.config import get_settings
from iplodds.data.cache import Cache

log = structlog.get_logger(__name__)

BASE_URL = "https://api.cricapi.com/v1"

# IPL team keywords used to identify IPL matches in currentMatches feed
_IPL_KEYWORDS = frozenset(
    {
        "CSK",
        "MI",
        "RCB",
        "KKR",
        "SRH",
        "DC",
        "PBKS",
        "RR",
        "GT",
        "LSG",
        "Chennai",
        "Mumbai",
        "Bangalore",
        "Kolkata",
        "Hyderabad",
        "Delhi",
        "Punjab",
        "Rajasthan",
        "Gujarat",
        "Lucknow",
    }
)

_cache: Cache | None = None


def _get_cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache


async def _fetch(path: str, **params: str | int) -> dict[str, Any]:
    """GET a CricAPI endpoint, injecting the configured API key."""
    s = get_settings()
    if not s.cricketdata_api_key:
        raise ValueError("IPLODDS_CRICKETDATA_API_KEY is not set")
    url = f"{BASE_URL}/{path}"
    all_params: dict[str, Any] = {"apikey": s.cricketdata_api_key, **params}
    async with httpx.AsyncClient(timeout=s.upstream_timeout_s) as client:
        resp = await client.get(url, params=all_params)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"CricAPI error ({path}): {data}")
    return data


# ---------------------------------------------------------------------------
# Series discovery
# ---------------------------------------------------------------------------


async def _get_ipl_series_id() -> str:
    """Return the CricAPI GUID for IPL 2026.

    Uses `cricketdata_ipl_series_id` from settings when present; otherwise
    auto-discovers via the series search endpoint and caches the result.
    """
    s = get_settings()
    if s.cricketdata_ipl_series_id:
        return s.cricketdata_ipl_series_id

    cache = _get_cache()
    cached = await cache.get("cricapi:series_id", s.cache_ttl_series_s)
    if cached:
        return cached["id"]

    # Search for IPL 2026 specifically, then fall back to any "2026" match.
    for search_term in ("Indian Premier League 2026", "IPL 2026", "Indian Premier League"):
        data = await _fetch("series", offset=0, search=search_term)
        for series in data.get("data") or []:
            name = (series.get("name") or "").lower()
            if "indian premier league" in name and "2026" in name:
                series_id: str = series["id"]
                await cache.set("cricapi:series_id", {"id": series_id})
                log.info("cricketdata.series_id_discovered", series_id=series_id)
                return series_id

    raise RuntimeError(
        "IPL 2026 series not found in CricAPI. "
        "Set IPLODDS_CRICKETDATA_IPL_SERIES_ID manually once the season is listed."
    )


# ---------------------------------------------------------------------------
# Series match list
# ---------------------------------------------------------------------------


async def get_series_matches() -> list[dict[str, Any]]:
    """Return all IPL 2026 match stubs from series_info, cached for 2 hours.

    Each stub: {id, name, date, dateTimeGMT, teams}
    """
    cache = _get_cache()
    s = get_settings()
    cached = await cache.get("cricapi:series_matches", s.cache_ttl_series_s)
    if cached:
        return cached  # type: ignore[return-value]

    series_id = await _get_ipl_series_id()
    data = await _fetch("series_info", id=series_id)
    matches_raw: list[dict[str, Any]] = (data.get("data") or {}).get("matchList") or []
    result = [
        {
            "id": m.get("id"),
            "name": m.get("name") or "",
            "date": m.get("date") or "",
            "dateTimeGMT": m.get("dateTimeGMT") or "",
            "teams": m.get("teams") or [],
        }
        for m in matches_raw
        if m.get("id")
    ]
    await cache.set("cricapi:series_matches", result)
    return result


# ---------------------------------------------------------------------------
# Scorecard helpers
# ---------------------------------------------------------------------------


def _match_score(match: dict[str, Any], hint: str) -> int:
    """Score how well a series-match stub matches the user's hint. Higher = better."""
    hint_upper = hint.upper()
    name_upper = (match.get("name") or "").upper()
    teams = [t.upper() for t in (match.get("teams") or [])]
    score = 0
    for token in re.split(r"[\s,/&]+", hint_upper):
        if not token:
            continue
        if token in name_upper:
            score += 2
        for team in teams:
            if token in team:
                score += 3
    return score


def _parse_dt(match: dict[str, Any]) -> datetime:
    raw = match.get("dateTimeGMT") or match.get("date") or ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return datetime.min.replace(tzinfo=datetime.UTC)


def _parse_scorecard(data: dict[str, Any]) -> dict[str, Any]:
    """Convert raw CricAPI match_scorecard response into a clean structured dict."""
    innings_list = []
    for inning in data.get("scorecard") or []:
        batting_rows = [
            {
                "batsman": b.get("batsman") or b.get("name") or "",
                "runs": b.get("r"),
                "balls": b.get("b"),
                "fours": b.get("4s"),
                "sixes": b.get("6s"),
                "strikeRate": b.get("sr"),
                "dismissal": b.get("out") or b.get("dismissal") or "",
            }
            for b in (inning.get("batting") or [])
        ]
        bowling_rows = [
            {
                "bowler": bw.get("bowler") or bw.get("name") or "",
                "overs": bw.get("o"),
                "maidens": bw.get("m"),
                "runs": bw.get("r"),
                "wickets": bw.get("w"),
                "economy": bw.get("eco") or bw.get("economy"),
                "wides": bw.get("wd"),
                "noBalls": bw.get("nb"),
            }
            for bw in (inning.get("bowling") or [])
        ]
        innings_list.append(
            {
                "inning": inning.get("inning") or "",
                "batting": batting_rows,
                "bowling": bowling_rows,
                "extras": inning.get("extras") or {},
                "total": inning.get("total") or {},
            }
        )
    return {
        "matchId": data.get("id"),
        "matchName": data.get("name"),
        "matchType": data.get("matchType"),
        "status": data.get("status"),
        "venue": data.get("venue"),
        "date": data.get("date") or data.get("dateTimeGMT"),
        "teams": data.get("teams") or [],
        "tossWinner": data.get("tossWinner"),
        "tossChoice": data.get("tossChoice"),
        "matchWinner": data.get("matchWinner"),
        "innings": innings_list,
        "score": data.get("score") or [],
    }


async def _fetch_scorecard_by_id(match_id: str, *, is_live: bool = False) -> dict[str, Any]:
    cache = _get_cache()
    s = get_settings()
    cache_key = f"cricapi:scorecard:{match_id}"
    ttl = s.cache_ttl_scorecard_live_s if is_live else s.cache_ttl_scorecard_post_s
    cached = await cache.get(cache_key, ttl)
    if cached:
        return cached  # type: ignore[return-value]
    raw = await _fetch("match_scorecard", id=match_id)
    result = _parse_scorecard(raw.get("data") or {})
    await cache.set(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_LIVE_HINTS = frozenset({"today", "live", "current", "now", "ongoing"})


async def get_scorecard(match_hint: str | None = None) -> dict[str, Any]:
    """Return the full batting and bowling scorecard for an IPL 2026 match.

    Args:
        match_hint: Optional team code (e.g. "RCB") or description (e.g. "CSK vs MI").
                    Pass None / omit to get the most recent completed or currently
                    live match.

    Returns:
        Structured dict: matchName, status, teams, innings (batting + bowling rows).
    """
    normalised_hint = (match_hint or "").strip().lower()
    want_live = not normalised_hint or normalised_hint in _LIVE_HINTS

    # --- Try currentMatches for a live IPL game ---
    if want_live:
        try:
            live_data = await _fetch("currentMatches", offset=0)
            for m in live_data.get("data") or []:
                name = m.get("name") or ""
                teams: list[str] = m.get("teams") or []
                combined = name + " ".join(teams)
                if any(kw.lower() in combined.lower() for kw in _IPL_KEYWORDS):
                    match_id = m.get("id")
                    if match_id:
                        log.info("cricketdata.using_live_match", match_id=match_id, name=name)
                        return await _fetch_scorecard_by_id(match_id, is_live=True)
        except Exception:
            log.warning("cricketdata.currentMatches_failed")

    # --- Fall back to series match list ---
    try:
        matches = await get_series_matches()
    except Exception as exc:
        return {"error": f"Could not retrieve IPL 2026 match list: {exc}"}

    if not matches:
        return {"error": "IPL 2026 match list is empty in CricAPI."}

    now = datetime.now(tz=datetime.UTC)
    sorted_matches = sorted(matches, key=_parse_dt, reverse=True)

    if match_hint and normalised_hint not in _LIVE_HINTS:
        best = max(sorted_matches, key=lambda m: _match_score(m, match_hint))
        if _match_score(best, match_hint) == 0:
            return {"error": f"No IPL 2026 match found matching '{match_hint}'."}
        match_id = best["id"]
        is_live = _parse_dt(best) >= now
    else:
        # Most recent past match
        past = [m for m in sorted_matches if _parse_dt(m) <= now]
        if not past:
            return {"error": "No completed IPL 2026 matches found yet."}
        match_id = past[0]["id"]
        is_live = False

    try:
        return await _fetch_scorecard_by_id(match_id, is_live=is_live)
    except Exception as exc:
        log.exception("cricketdata.scorecard_fetch_failed", match_id=match_id)
        return {"error": f"Failed to fetch scorecard: {exc}"}


# ---------------------------------------------------------------------------
# Latest result — lightweight endpoint for the UI badge
# ---------------------------------------------------------------------------

# CricAPI returns full team names; map to the short codes used throughout the UI.
# Both old and new RCB names are included (they rebranded Bangalore→Bengaluru in 2024).
_TEAM_NAME_TO_CODE: dict[str, str] = {
    "royal challengers bengaluru": "RCB",
    "royal challengers bangalore": "RCB",
    "mumbai indians": "MI",
    "chennai super kings": "CSK",
    "kolkata knight riders": "KKR",
    "sunrisers hyderabad": "SRH",
    "delhi capitals": "DC",
    "punjab kings": "PBKS",
    "rajasthan royals": "RR",
    "gujarat titans": "GT",
    "lucknow super giants": "LSG",
}


def _name_to_code(name: str) -> str:
    """Convert a full IPL team name to its short code (e.g. 'Mumbai Indians' → 'MI')."""
    return _TEAM_NAME_TO_CODE.get((name or "").strip().lower(), "")


async def get_latest_result() -> dict[str, Any]:
    """Return the most recent IPL match result or live status for the UI badge.

    Tries currentMatches first (covers live games and very recently completed ones),
    then falls back to the series match list + scorecard for the last completed game.

    Returns:
        {
            "isLive": bool,
            "status": str,          # e.g. "RCB won by 2 wickets" or "Match in progress"
            "winnerCode": str | None,  # short code, e.g. "RCB"
            "loserCode": str | None,   # short code, e.g. "MI"
            "teams": list[str],        # short codes of both teams
            "matchName": str,
        }
        On failure: {"error": str}
    """
    cache = _get_cache()
    s = get_settings()

    # Serve from cache (5-min TTL) to stay within the 100 req/day free-plan limit.
    # A cache miss triggers at most 2 upstream calls (currentMatches + scorecard).
    cached = await cache.get("cricapi:latest_result", 300)
    if cached and "error" not in cached:
        return cached  # type: ignore[return-value]

    # --- 1. currentMatches: catches live games and very recently finished ones ---
    try:
        live_data = await _fetch("currentMatches", offset=0)
        for m in live_data.get("data") or []:
            name = m.get("name") or ""
            teams_raw: list[str] = m.get("teams") or []
            combined = name + " " + " ".join(teams_raw)
            if not any(kw.lower() in combined.lower() for kw in _IPL_KEYWORDS):
                continue

            status = m.get("status") or ""
            winner_raw = m.get("matchWinner") or ""
            winner_code = _name_to_code(winner_raw)
            teams_coded = [_name_to_code(t) or t for t in teams_raw]
            loser_code = (
                next((t for t in teams_coded if t != winner_code), None) if winner_code else None
            )
            result: dict[str, Any] = {
                "isLive": not bool(winner_code),
                "status": status,
                "winnerCode": winner_code or None,
                "loserCode": loser_code,
                "teams": teams_coded,
                "matchName": name,
            }
            log.info(
                "cricketdata.latest_result.from_currentMatches",
                match=name,
                winner=winner_code,
                is_live=result["isLive"],
            )
            await cache.set("cricapi:latest_result", result)
            return result
    except Exception:
        log.warning("cricketdata.latest_result.currentMatches_failed")

    # --- 2. Series match list + scorecard for the last completed game ---
    try:
        matches = await get_series_matches()
        now = datetime.now(tz=datetime.UTC)
        past = sorted(
            [m for m in matches if _parse_dt(m) <= now],
            key=_parse_dt,
            reverse=True,
        )
        if not past:
            return {"error": "No completed IPL 2026 matches found yet."}

        scorecard = await _fetch_scorecard_by_id(past[0]["id"], is_live=False)
        winner_raw = scorecard.get("matchWinner") or ""
        winner_code = _name_to_code(winner_raw)
        teams_raw = scorecard.get("teams") or []
        teams_coded = [_name_to_code(t) or t for t in teams_raw]
        loser_code = (
            next((t for t in teams_coded if t != winner_code), None) if winner_code else None
        )
        result = {
            "isLive": False,
            "status": scorecard.get("status") or "",
            "winnerCode": winner_code or None,
            "loserCode": loser_code,
            "teams": teams_coded,
            "matchName": scorecard.get("matchName") or "",
        }
        log.info(
            "cricketdata.latest_result.from_scorecard",
            match=result["matchName"],
            winner=winner_code,
        )
        await cache.set("cricapi:latest_result", result)
        return result
    except Exception as exc:
        log.exception("cricketdata.latest_result.series_fallback_failed")
        return {"error": f"Could not determine latest match result: {exc}"}
