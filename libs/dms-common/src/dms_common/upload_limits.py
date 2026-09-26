"""Shared max-upload-size enforcement (Phase 61 Session 2) - the identical
gap (an unbounded `await file.read()`/`await request.body()` before any
expensive processing, with no size cap anywhere in-process or at any layer
in front of these services) was found independently in three unrelated
services (`virus-scan-service`, `rendering-service`, `storage-service`),
unlike every other finding in this gap-analysis round, which was genuinely
service-specific - worth a shared helper instead of three near-identical
copies.

**Why a raw ASGI middleware, not `BaseHTTPMiddleware`/a per-endpoint check**:
for a `File(...)`/`UploadFile` parameter, FastAPI/Starlette already fully
reads the upload into memory (or a spooled temp file) to populate
`UploadFile` BEFORE the endpoint function even runs - a per-endpoint check
after that point is already too late to prevent the read itself.
`BaseHTTPMiddleware` cannot help either: it hands the endpoint a `Request`
whose body it would need to fully consume itself to count bytes, at which
point that body is no longer available downstream (Starlette only caches
`request.body()` if read via that exact method, not via manually iterating
`request.stream()`). A raw ASGI middleware wrapping `receive` directly
counts every byte exactly once, with no double-consumption risk, and can
reject with `413` before routing/multipart-parsing ever starts - one
`app.add_middleware(...)` call per service instead of a call at every
upload endpoint.

**Since Phase 78 Session 2** (gap-analysis round after Phase 77): closes a
real chunked-transfer-encoding bypass. The original version only inspected
the declared `Content-Length` header - a request with no `Content-Length`
at all (a genuinely chunked-encoded body) skipped the check entirely, an
honestly-documented but real gap at the time. The `Content-Length` fast
path is unchanged (rejects immediately from the header alone, no buffering,
covers the overwhelming majority of real upload clients, which do send
`Content-Length`). For the remaining case - no `Content-Length`, or one
that lies - this now buffers incoming body chunks itself, byte-counted as
they arrive, up to `max_bytes + 1`; if that bound is reached before the
body ends, it rejects with `413` immediately (bounded memory cost, same
order of magnitude as what a legitimate request under the limit would
already cost the downstream app to buffer itself) and never invokes the
wrapped application at all. Only a request actually within the limit is
ever handed to the app - fully buffered by then, replayed back through a
`receive` substitute so the app sees the exact same messages it always
did."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_bytes: int | None = int(content_length)
            except ValueError:
                declared_bytes = None
            if declared_bytes is not None and declared_bytes > self._max_bytes:
                await self._reject(send, declared_bytes)
                return

        buffered: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # e.g. an early `http.disconnect` - nothing more to buffer, let
                # the app see this exact message via the replay below.
                buffered_message = message
                break
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self._max_bytes:
                await self._reject(send, total)
                return
            buffered.append(chunk)
            if not message.get("more_body", False):
                buffered_message = None
                break

        queue: list[Message] = [
            {"type": "http.request", "body": chunk, "more_body": i < len(buffered) - 1}
            for i, chunk in enumerate(buffered)
        ]
        if buffered_message is not None:
            queue.append(buffered_message)

        async def _replay_receive() -> Message:
            if queue:
                return queue.pop(0)
            return await receive()

        await self.app(scope, _replay_receive, send)

    async def _reject(self, send: Send, declared_bytes: int) -> None:
        detail = (
            f'{{"detail": "Anfrage zu groß ({declared_bytes} Bytes) - Maximum sind '
            f'{self._max_bytes} Bytes"}}'
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": detail})
