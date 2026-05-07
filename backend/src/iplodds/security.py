"""Security middleware: headers, body-size cap, structured request logging."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

log = structlog.get_logger(__name__)


SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cache-Control": "no-store",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response


class BodySizeLimitMiddleware:
    """Pure ASGI middleware that enforces a max body size and replays the
    buffered body via the receive channel so downstream handlers can read it.

    Implemented as raw ASGI (not BaseHTTPMiddleware) because BaseHTTPMiddleware
    does not expose a supported way to re-inject a consumed request body —
    setting ``request._stream`` is a no-op in Starlette and causes downstream
    body reads to return empty, which surfaces as HTTP 422 on JSON endpoints.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        from starlette.responses import JSONResponse

        # Fast-path: reject oversize bodies declared via Content-Length.
        for name, value in scope.get("headers") or []:
            if name == b"content-length" and value.isdigit() and int(value) > self.max_bytes:
                response = JSONResponse({"error": "request body too large"}, status_code=413)
                await response(scope, receive, send)
                return

        method = scope.get("method", "GET").upper()
        if method not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        # Buffer the full body, enforcing the cap on chunked / unknown-length streams.
        chunks: list[bytes] = []
        total = 0
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"") or b""
            total += len(chunk)
            if total > self.max_bytes:
                response = JSONResponse({"error": "request body too large"}, status_code=413)
                await response(scope, receive, send)
                return
            chunks.append(chunk)
            more_body = message.get("more_body", False)

        body = b"".join(chunks)
        replayed = False

        async def replay_receive() -> dict:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        start = time.perf_counter()
        structlog.contextvars.bind_contextvars(
            request_id=rid, path=request.url.path, method=request.method
        )
        try:
            response = await call_next(request)
            dur_ms = int((time.perf_counter() - start) * 1000)
            log.info("request", status=response.status_code, duration_ms=dur_ms)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            structlog.contextvars.clear_contextvars()
