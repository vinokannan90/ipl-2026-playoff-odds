"""Tools the Q&A agent can call. Each is async and returns JSON-serializable data.

Tools are intentionally narrow and side-effect free (read-only) so the agent
cannot cause harm by mis-calling them.
"""

from __future__ import annotations

from typing import Any

from iplodds.agents import leverage as leverage_agent
from iplodds.agents import priors as priors_agent
from iplodds.data import iplt20_client


async def get_standings_tool() -> dict[str, Any]:
    """Return current league standings."""
    raw = await iplt20_client.get_standings()
    rows = raw.get("points", [])
    return {
        "teams": [
            {
                "code": r.get("TeamCode"),
                "name": r.get("TeamName"),
                "pts": int(r.get("Points") or 0),
                "w": int(r.get("Wins") or 0),
                "l": int(r.get("Loss") or 0),
                "nr": int(r.get("NoResult") or 0),
                "nrr": float(r.get("NetRunRate") or 0),
            }
            for r in rows
        ]
    }


async def _team_id_to_code() -> dict[str, str]:
    """Build a TeamID -> TeamCode map from the standings feed.

    The schedule feed has empty HomeTeamCode/AwayTeamCode strings, so we have
    to resolve codes via the standings (which do carry both ID and code).
    """
    standings = await iplt20_client.get_standings()
    out: dict[str, str] = {}
    for row in standings.get("points", []):
        tid = str(row.get("TeamID") or "")
        code = (row.get("TeamCode") or "").upper()
        if tid and code:
            out[tid] = code
    return out


async def get_remaining_fixtures_tool(team_code: str | None = None) -> dict[str, Any]:
    """Return remaining fixtures, optionally filtered by team code."""
    sched = await iplt20_client.get_schedule()
    id_to_code = await _team_id_to_code()
    wanted = team_code.upper() if team_code else None
    out = []
    for m in sched.get("Matchsummary", []):
        status = (m.get("MatchStatus") or "").lower()
        if status in {"post", "completed", "result"}:
            continue
        home_id = str(m.get("HomeTeamID") or "")
        away_id = str(m.get("AwayTeamID") or "")
        home = (m.get("HomeTeamCode") or "").upper() or id_to_code.get(home_id, "")
        away = (m.get("AwayTeamCode") or "").upper() or id_to_code.get(away_id, "")
        if wanted and wanted not in {home, away}:
            continue
        out.append({
            "date": m.get("MatchDate"),
            "home": home,
            "away": away,
            "homeName": m.get("HomeTeamName"),
            "awayName": m.get("AwayTeamName"),
            "matchId": str(m.get("MatchID")),
        })
    return {"fixtures": out, "count": len(out)}


async def get_priors_tool() -> dict[str, Any]:
    """Return cached LLM-derived per-match win probabilities."""
    return await priors_agent.compute_priors()


async def get_leverage_tool(team_code: str | None = None, top_n: int = 5) -> dict[str, Any]:
    """Return matches ranked by playoff-probability swing (optionally focused on a team)."""
    return await leverage_agent.compute_leverage(
        top_n=max(1, min(10, top_n)),
        team_code=team_code,
        n_sims=1500,
    )


_LIVE_STATUSES = {"live", "inprogress", "in progress", "innings break", "mid-innings"}


async def get_live_match_tool() -> dict[str, Any]:
    """Return live scorecard data for the currently in-progress IPL match.

    Fetches the schedule feed with a short (30-second) cache TTL so the data
    is reasonably current during a live game. If no match is live, returns a
    clear message so the agent can tell the user.
    """
    sched = await iplt20_client.get_live_scorecard()
    id_to_code = await _team_id_to_code()

    for m in sched.get("Matchsummary", []):
        status = (m.get("MatchStatus") or "").lower().strip()
        if status not in _LIVE_STATUSES:
            continue

        home_id = str(m.get("HomeTeamID") or "")
        away_id = str(m.get("AwayTeamID") or "")
        bat1_id = str(m.get("FirstBattingTeamID") or "")
        bat2_id = str(m.get("SecondBattingTeamID") or "")
        home = (m.get("HomeTeamCode") or "").upper() or id_to_code.get(home_id, home_id)
        away = (m.get("AwayTeamCode") or "").upper() or id_to_code.get(away_id, away_id)
        bat1_code = (
            (m.get("FirstBattingTeamCode") or "").upper()
            or id_to_code.get(bat1_id, bat1_id)
        )
        bat2_code = (
            (m.get("SecondBattingTeamCode") or "").upper()
            or id_to_code.get(bat2_id, bat2_id)
        )

        return {
            "live": True,
            "matchName": m.get("MatchName") or f"{home} vs {away}",
            "venue": m.get("GroundName"),
            "status": m.get("MatchStatus"),
            "matchProgress": m.get("MatchProgress"),
            "currentInnings": m.get("CurrentInnings"),
            "toss": m.get("TossText") or m.get("TossDetails"),
            "innings1": {
                "battingTeam": bat1_code,
                "summary": m.get("FirstBattingSummary"),
            },
            "innings2": {
                "battingTeam": bat2_code,
                "summary": m.get("SecondBattingSummary"),
            },
            "batting": {
                "striker": m.get("CurrentStrikerName"),
                "strikerRuns": m.get("StrikerRuns"),
                "strikerBalls": m.get("StrikerBalls"),
                "strikerFours": m.get("StrikerFours"),
                "strikerSixes": m.get("StrikerSixes"),
                "strikerSR": m.get("StrikerSR"),
                "nonStriker": m.get("CurrentNonStrikerName"),
                "nonStrikerRuns": m.get("NonStrikerRuns"),
                "nonStrikerBalls": m.get("NonStrikerBalls"),
            },
            "bowling": {
                "bowler": m.get("CurrentBowlerName"),
                "overs": m.get("BowlerOvers"),
                "wickets": m.get("BowlerWickets"),
                "runs": m.get("BowlerRuns"),
                "economy": m.get("BowlerEconomy"),
                "maidens": m.get("BowlerMaidens"),
            },
            "chaseContext": m.get("ChasingText"),
            "projectedScore": m.get("ProjectedScore") or m.get("2ndProjectedScore"),
            "currentRunRate": m.get("1RunRate") or m.get("2RunRate"),
            "revisedTarget": m.get("RevisedTarget"),
            "revisedOvers": m.get("RevisedOver"),
            "dataAgeSeconds": 30,
        }

    return {
        "live": False,
        "message": (
            "No IPL match is currently in progress. "
            "Check the schedule for upcoming fixtures."
        ),
    }


# OpenAI tool schemas
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_standings",
            "description": "Return current IPL 2026 league standings.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_remaining_fixtures",
            "description": (
                "Return remaining IPL 2026 fixtures, optionally filtered"
                " by team code (e.g. 'RCB')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "team_code": {"type": "string", "description": "3-letter team code"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_priors",
            "description": "Return per-match LLM-derived home-team win probabilities.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_leverage",
            "description": (
                "Return remaining matches ranked by how much they swing playoff"
                " probabilities. Optionally focus on a team_code (e.g. 'RCB')"
                " to rank by that team's leverage."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "team_code": {"type": "string", "description": "Optional 3-letter team code"},
                    "top_n": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_live_match",
            "description": (
                "Return live scorecard for the IPL match currently in progress: "
                "batters, bowler, innings summaries, run-rate, chasing context, "
                "projected score, and toss. Data is at most 30 seconds old. "
                "Returns {live: false} if no match is currently being played."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
]


TOOL_DISPATCH = {
    "get_standings": lambda **kw: get_standings_tool(),
    "get_remaining_fixtures": lambda **kw: get_remaining_fixtures_tool(
        team_code=kw.get("team_code")
    ),
    "get_priors": lambda **kw: get_priors_tool(),
    "get_leverage": lambda **kw: get_leverage_tool(
        team_code=kw.get("team_code"), top_n=int(kw.get("top_n") or 5)
    ),
    "get_live_match": lambda **kw: get_live_match_tool(),
}
