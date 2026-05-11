"""Routers for data, priors, agent, and leverage endpoints."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from iplodds.agents import leverage as leverage_agent
from iplodds.agents import priors as priors_agent
from iplodds.agents import qa as qa_agent
from iplodds.agents import scout as scout_agent
from iplodds.config import Settings, get_settings
from iplodds.data import iplt20_client
from iplodds.data import cricketdata_client

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/api")


@router.get("/standings")
@limiter.limit(lambda: get_settings().rate_limit_default)
async def standings(request: Request) -> dict:  # noqa: ARG001
    return await iplt20_client.get_standings()


@router.get("/schedule")
@limiter.limit(lambda: get_settings().rate_limit_default)
async def schedule(request: Request) -> dict:  # noqa: ARG001
    return await iplt20_client.get_schedule()


def _parse_overs(s: str) -> float:
    """Convert "18.2" or "18" to a float for progress comparisons."""
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


_SCORE_RE = re.compile(r"(\d+/\d+)\s*\(([0-9.]+)\s*Ov", re.IGNORECASE)


def _parse_score_summary(summary: str) -> dict[str, str]:
    """Parse "223/3 (19.2 Ov)" or "223/3 (19.2 Overs)" into score/overs."""
    m = _SCORE_RE.search(summary)
    if m:
        return {"score": m.group(1), "overs": m.group(2)}
    return {"score": summary.strip(), "overs": ""}


@router.get("/live")
@limiter.limit(lambda: get_settings().rate_limit_default)
async def live_score(request: Request) -> dict:  # noqa: ARG001
    """Returns in-progress match scores using the short-TTL live feed (30 s cache).

    Returns at most the two concurrent matches that could happen on a weekend
    double-header, sorted by overs played descending so the match closest to
    finishing comes first.
    """
    data = await iplt20_client.get_live_scorecard()
    matches = data.get("Matchsummary", [])
    live = []
    for m in matches:
        status = (m.get("MatchStatus") or "").lower().strip()
        if "live" not in status and "progress" not in status:
            continue

        first_code = m.get("FirstBattingTeamCode") or ""
        second_code = m.get("SecondBattingTeamCode") or ""
        current_innings = int(m.get("CurrentInnings") or 1)

        # Build innings list from flat numbered summary fields in the feed.
        # Fields: "1Summary" = "223/3 (19.2 Overs)", "2Summary" = "" if not started.
        innings = []
        for inning_num in (1, 2):
            raw = (
                m.get(f"{inning_num}Summary")
                or (
                    m.get("FirstBattingSummary")
                    if inning_num == 1
                    else m.get("SecondBattingSummary")
                )
                or ""
            )
            if not raw:
                continue
            team_code = first_code if inning_num == 1 else second_code
            parsed = _parse_score_summary(raw)
            innings.append(
                {
                    "inningNum": inning_num,
                    "teamCode": team_code,
                    "score": parsed["score"],
                    "overs": parsed["overs"],
                }
            )

        # Compute progress for sorting: 2nd-innings matches rank higher than 1st-innings.
        active = next((i for i in innings if i["inningNum"] == current_innings), None)
        overs_played = _parse_overs(active["overs"]) if active else 0.0
        if current_innings == 2:
            overs_played += 20.0

        live.append(
            {
                "matchId": str(m.get("MatchID", "")),
                "homeCode": first_code,
                "awayCode": second_code,
                "currentInnings": current_innings,
                "innings": innings,
                "chasingText": m.get("ChasingText") or "",
                "matchName": m.get("MatchName") or "",
                "oversPlayed": overs_played,
            }
        )
    # Most advanced match first (weekend dual-match edge case)
    live.sort(key=lambda x: x["oversPlayed"], reverse=True)
    return {"live": live}


@router.get("/latest-result")
@limiter.limit(lambda: get_settings().rate_limit_default)
async def latest_result(request: Request) -> dict:  # noqa: ARG001
    """Return the most recent IPL match result (or live status) from cricketdata.org.

    Used by the frontend to display an authoritative "Latest: X beat Y" badge that
    is independent of the iplt20.com schedule feed's field structure.
    Cached for 5 minutes server-side; returns {"error": str} if unavailable.
    """
    s = get_settings()
    if not s.cricketdata_api_key:
        raise HTTPException(503, "cricketdata API key not configured")
    return await cricketdata_client.get_latest_result()


@router.get("/priors")
@limiter.limit(lambda: get_settings().rate_limit_default)
async def priors(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> dict:  # noqa: ARG001
    if not settings.feature_priors:
        raise HTTPException(404, "feature disabled")
    return await priors_agent.compute_priors()


@router.get("/leverage")
@limiter.limit(lambda: get_settings().rate_limit_default)
async def leverage(
    request: Request,  # noqa: ARG001
    settings: Annotated[Settings, Depends(get_settings)],
    team: str | None = None,
    top_n: int = 5,
    n_sims: int = 2000,
) -> dict:
    if not settings.feature_leverage:
        raise HTTPException(404, "feature disabled")
    if top_n < 1 or top_n > 20:
        raise HTTPException(422, "top_n must be 1..20")
    if n_sims < 500 or n_sims > 20000:
        raise HTTPException(422, "n_sims must be 500..20000")
    if team is not None and (len(team) > 5 or not team.isalpha()):
        raise HTTPException(422, "team must be a short alphabetic code")
    return await leverage_agent.compute_leverage(top_n=top_n, team_code=team, n_sims=n_sims)


@router.get("/scout")
@limiter.limit(lambda: get_settings().rate_limit_default)
async def scout(
    request: Request, settings: Annotated[Settings, Depends(get_settings)], team: str | None = None
) -> dict:  # noqa: ARG001
    if not settings.feature_scout:
        raise HTTPException(404, "feature disabled")
    return await scout_agent.fetch_signals(team)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=280)


@router.post("/agent/ask")
@limiter.limit(lambda: get_settings().rate_limit_agent)
async def agent_ask(
    request: Request,  # noqa: ARG001
    body: AskRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    if not settings.feature_agent:
        raise HTTPException(404, "feature disabled")
    return await qa_agent.ask(body.question)
