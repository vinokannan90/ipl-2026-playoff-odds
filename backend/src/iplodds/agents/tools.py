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


async def get_remaining_fixtures_tool(team_code: str | None = None) -> dict[str, Any]:
    """Return remaining fixtures, optionally filtered by team code."""
    sched = await iplt20_client.get_schedule()
    out = []
    for m in sched.get("Matchsummary", []):
        status = (m.get("MatchStatus") or "").lower()
        if status in {"post", "completed", "result"}:
            continue
        home = m.get("HomeTeamCode")
        away = m.get("AwayTeamCode")
        if team_code and team_code.upper() not in {(home or "").upper(), (away or "").upper()}:
            continue
        out.append({"date": m.get("MatchDate"), "home": home, "away": away, "matchId": str(m.get("MatchID"))})
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
            "description": "Return remaining IPL 2026 fixtures, optionally filtered by team code (e.g. 'RCB').",
            "parameters": {
                "type": "object",
                "properties": {"team_code": {"type": "string", "description": "3-letter team code"}},
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
            "description": "Return remaining matches ranked by how much they swing playoff probabilities. Optionally focus on a team_code (e.g. 'RCB') to rank by that team's leverage.",
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
]


TOOL_DISPATCH = {
    "get_standings": lambda **kw: get_standings_tool(),
    "get_remaining_fixtures": lambda **kw: get_remaining_fixtures_tool(team_code=kw.get("team_code")),
    "get_priors": lambda **kw: get_priors_tool(),
    "get_leverage": lambda **kw: get_leverage_tool(
        team_code=kw.get("team_code"), top_n=int(kw.get("top_n") or 5)
    ),
}
