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

SYSTEM = """You are an IPL 2026 playoff-odds analyst.

Style: concise, factual, neutral. 2-4 sentences max.
Always cite the data you used (standings/fixtures/priors).
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

    for round_idx in range(MAX_ROUNDS):
        try:
            resp = await llm.chat(messages=messages, tools=TOOL_SCHEMAS, max_tokens=600)
        except Exception:
            log.exception("agent.llm_failed")
            return {"text": "The agent is temporarily unavailable. Please try again.", "citations": []}

        msg = resp.choices[0].message
        tool_calls = msg.tool_calls or []
        if not tool_calls:
            return {"text": (msg.content or "").strip(), "citations": citations}

        # Append assistant message preserving tool_calls
        messages.append({
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
        })

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
                    citations.append({"label": f"{name}({', '.join(f'{k}={v}' for k, v in args.items())})", "url": ""})
                except Exception as e:
                    log.exception("agent.tool_failed", tool=name)
                    tool_result = {"error": f"tool {name} failed: {type(e).__name__}"}
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": orjson.dumps(tool_result).decode(),
            })

    return {"text": "Reached the tool-call limit without producing a final answer.", "citations": citations}
