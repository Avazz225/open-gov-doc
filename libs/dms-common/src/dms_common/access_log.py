"""One structured log line per HTTP request (P94-S2, ADR 0251).

Replaces uvicorn's plain-text access log: written inside the request - so
inside its trace when tracing is on - with method, route, status and duration
as JSON fields Loki can filter on. Health and scrape endpoints are left out;
the stack polls them every few seconds.
"""

import logging
import time

logger = logging.getLogger("dms.access")

SKIPPED_PATHS = frozenset({"/healthz", "/metrics"})


class AccessLogMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("path") in SKIPPED_PATHS:
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = 500

        async def send_wrapper(message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = getattr(scope.get("route"), "path", None)
            logger.log(
                logging.WARNING if status >= 500 else logging.INFO,
                "%s %s %s",
                scope.get("method"),
                route or scope.get("path"),
                status,
                extra={
                    "http_method": scope.get("method"),
                    "http_route": route,
                    "http_path": scope.get("path"),
                    "http_status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
