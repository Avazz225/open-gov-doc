from dms_common.upload_limits import MaxBodySizeMiddleware
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient


async def _echo(request):
    body = await request.body()
    return PlainTextResponse(f"received {len(body)} bytes")


def _make_app(*, max_bytes: int) -> Starlette:
    app = Starlette(routes=[Route("/upload", _echo, methods=["POST"])])
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=max_bytes)
    return app


def test_request_under_limit_passes_through():
    client = TestClient(_make_app(max_bytes=1024))
    response = client.post("/upload", content=b"x" * 100)
    assert response.status_code == 200
    assert response.text == "received 100 bytes"


def test_request_over_content_length_limit_rejected_with_413():
    """Phase 61 Session 2 (ADR 0187) - the middleware rejects based on the
    declared `Content-Length` header BEFORE the route/body is ever
    reached, not after reading the oversized body into memory."""
    client = TestClient(_make_app(max_bytes=10))
    response = client.post("/upload", content=b"x" * 100)
    assert response.status_code == 413
    assert "100 Bytes" in response.json()["detail"]


def test_request_exactly_at_limit_passes_through():
    client = TestClient(_make_app(max_bytes=10))
    response = client.post("/upload", content=b"x" * 10)
    assert response.status_code == 200


def test_chunked_request_over_limit_without_content_length_is_rejected_with_413():
    """Phase 78 Session 2 (gap-analysis round after Phase 77) - closes what
    was previously an accepted residual gap: a request with no
    `Content-Length` header at all (a genuinely chunked-encoded body) used
    to skip the size check entirely. Now byte-counted as the body streams
    in and rejected once the bound is exceeded, before the app is ever
    invoked."""
    client = TestClient(_make_app(max_bytes=10))

    def _generate():
        yield b"x" * 100

    response = client.post("/upload", content=_generate())
    assert response.status_code == 413


def test_chunked_request_under_limit_without_content_length_still_reaches_the_app():
    """The other half of the Phase 78 Session 2 fix: a genuinely
    chunked-encoded body that fits within the limit must still reach the
    real endpoint afterward, byte-for-byte identical to what it sent -
    proves the buffer-then-replay path doesn't corrupt or drop data for the
    legitimate case."""
    client = TestClient(_make_app(max_bytes=1024))

    def _generate():
        yield b"x" * 30
        yield b"y" * 20

    response = client.post("/upload", content=_generate())
    assert response.status_code == 200
    assert response.text == "received 50 bytes"
