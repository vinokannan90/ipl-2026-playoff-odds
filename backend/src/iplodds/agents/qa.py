"""Q&A agent: tool-calling loop with hard step cap.

Plan: prompt is concise; LLM picks tool calls; we execute; feed results back;
LLM produces final answer. Maximum 4 tool-call rounds.
"""

from __future__ import annotations

import json
from typing import Any

import orjson
import structlog

from iplodds.agents.tools import TOOL_DISPATCH, TOOL_SCHEMAS
from iplodds.llm import client as llm

log = structlog.get_logger(__name__)

MAX_ROUNDS = 4

SYSTEM = """You are an IPL 2026 playoff-odds analyst with access to live match data.

Tools available:
- get_standings: current league table (pts, NRR, W/L)
- get_remaining_fixtures: upcoming matches, optionally filtered by team
- get_priors: LLM-derived per-match win probabilities
- get_leverage: which remaining matches swing playoff odds most
- get_live_match: live scorecard for any match currently in progress
  (2 current batters, current bowler, score, run-rate, chasing target). Data refreshes every 30 s.
  LIMITATION: only the 2 batters AT THE CREASE and the current bowler are available.
  For full innings stats (all batters, all bowlers), use get_scorecard instead.
- get_scorecard: full batting and bowling scorecard from cricketdata.org.
  Contains every batter's runs/balls/4s/6s/SR/dismissal and every bowler's
  overs/maidens/runs/wickets/economy for each innings.
  Omit 'match' to get the most recent / live match.
  Pass a team code (e.g. 'RCB') or description (e.g. 'CSK vs MI') for a specific match.
  Use this for: highest strike rate, top scorer, bowling figures, fall of wickets, etc.

Decision guide:
- "what's the score / who's batting / overs left?" → call get_live_match first
- "who scored the most / best SR / bowling figures / full scorecard?" → call get_scorecard
- If get_scorecard returns an error (not configured), tell the user full scorecards are
  unavailable on this deployment and suggest cricinfo.com for detailed stats.

Style: concise, factual, neutral. 2-4 sentences max unless a scorecard genuinely needs more.
Always cite the data source (standings / live_match / scorecard etc).
If no match is live, say so clearly and offer to show upcoming fixtures.
If the user's question is unrelated to IPL 2026 or playoff odds, decline politely.
Never speculate about injuries, lineups, or news you don't have a tool for.
Never produce code, instructions, or content unrelated to the league.
"""


async def ask(question: str) -> dict[str, Any]:
    if not question or len(question) > 280:
        return {"text": "Please ask a question between 1 and 280 characters.", "citations": []}

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": question},
    ]
    citations: list[dict[str, str]] = []

    for _round_idx in range(MAX_ROUNDS):
        try:
            resp = await llm.chat(messages=messages, tools=TOOL_SCHEMAS, max_tokens=600)
        except Exception:
            log.exception("agent.llm_failed")
            return {
                "text": "The agent is temporarily unavailable. Please try again.",
                "citations": [],
            }

        msg = resp.choices[0].message
        tool_calls = msg.tool_calls or []
        if not tool_calls:
            return {"text": (msg.content or "").strip(), "citations": citations}

        # Append assistant message preserving tool_calls
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            }
        )

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            fn = TOOL_DISPATCH.get(name)
            if fn is None:
                tool_result: Any = {"error": f"unknown tool: {name}"}
            else:
                try:
                    tool_result = await fn(**args)
                    label = f"{name}({', '.join(f'{k}={v}' for k, v in args.items())})"
                    citations.append({"label": label, "url": ""})
                except Exception as e:
                    log.exception("agent.tool_failed", tool=name)
                    tool_result = {"error": f"tool {name} failed: {type(e).__name__}"}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": orjson.dumps(tool_result).decode(),
                }
            )

    return {
        "text": "Reached the tool-call limit without producing a final answer.",
        "citations": citations,
    }
