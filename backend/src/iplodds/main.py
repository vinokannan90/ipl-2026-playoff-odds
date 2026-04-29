"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.responses import JSONResponse

from iplodds.config import get_settings
from iplodds.data.cache import get_cache
from iplodds.routers import limiter, router
from iplodds.security import (
    BodySizeLimitMiddleware,
    RequestLogMiddleware,
    SecurityHeadersMiddleware,
)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    s = get_settings()
    _configure_logging(s.log_level)
    yield
    await get_cache().aclose()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="IPL 2026 Playoff Odds API",
        version="0.1.0",
        docs_url="/docs" if s.env == "dev" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if s.env == "dev" else None,
        lifespan=lifespan,
    )

    # Order matters: outermost middleware first.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestLogMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=s.request_max_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
        max_age=600,
    )
    app.add_middleware(SlowAPIMiddleware)
    app.state.limiter = limiter

    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:  # noqa: ARG001
        return JSONResponse({"error": "rate_limited"}, status_code=429)

    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok", "env": s.env}

    app.include_router(router)
    return app


app = create_app()
