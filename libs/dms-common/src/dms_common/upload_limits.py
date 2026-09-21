"""Shared max-upload-size enforcement (Phase 61 Session 2) - the identical
gap (an unbounded `await file.read()`/`await request.body()` before any
expensive processing, with no size cap anywhere in-process or at any layer
in front of these services) was found independently in three unrelated
services (`virus-scan-service`, `rendering-service`, `storage-service`),
unlike every other finding in this gap-analysis round, which was genuinely
service-specific - worth a shared helper instead of three near-identical
copies.

**Why a middleware, not a per-endpoint check**: for a `File(...)`/
`UploadFile` parameter, FastAPI/Starlette already fully reads the upload
into memory (or a spooled temp file) to populate `UploadFile` BEFORE the
endpoint function even runs - a per-endpoint check after that point is
already too late to prevent the read itself. `MaxBodySizeMiddleware`
inspects the `Content-Length` request header and rejects with `413`
BEFORE routing/multipart-parsing ever starts, for every route uniformly -
one `app.add_middleware(...)` call per service instead of a call at every
upload endpoint.

**Accepted residual gap**: a request with no `Content-Length` header at
all (e.g. genuinely chunked-encoded, which real upload clients rarely if
ever use for file uploads) is NOT caught by this middleware - a narrower,
honestly-documented limitation, not a claim of full streaming enforcement."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_bytes = int(content_length)
            except ValueError:
                declared_bytes = None
            if declared_bytes is not None and declared_bytes > self._max_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": (
                            f"Anfrage zu groß ({declared_bytes} Bytes) - Maximum sind "
                            f"{self._max_bytes} Bytes"
                        )
                    },
                )
        return await call_next(request)
