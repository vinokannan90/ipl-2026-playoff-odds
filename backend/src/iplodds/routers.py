"""Routers for data, priors, agent, and leverage endpoints."""

from __future__ import annotations

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
