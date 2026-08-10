from gateway_service.upstream import filter_headers
from starlette.datastructures import Headers


def test_filter_headers_drops_hop_by_hop_headers():
    headers = Headers(raw=[(b"connection", b"keep-alive"), (b"content-type", b"application/json")])
    assert filter_headers(headers) == {"content-type": "application/json"}


def test_filter_headers_drops_client_supplied_x_dms_headers():
    """Sicherheitsfund (P14-S11-Live-Verifikation, siehe ADR 0049): ein Client
    darf keinen eigenen `X-DMS-*`-Header mitschicken, der neben dem später
    von `proxy()` injizierten echten Header bestehen bleibt - Python-Dict-
    Keys sind case-sensitiv, ein `x-dms-principal` (ASGI-normalisiert,
    lowercase) und ein `X-DMS-Principal` (aus `identity_headers`) sind zwei
    verschiedene Schlüssel, wenn dieser Filter ihn nicht vorher entfernt."""
    headers = Headers(
        raw=[
            (b"x-dms-principal", b"attacker-spoofed-id"),
            (b"x-dms-roles", b"dms-admin"),
            (b"x-dms-maintenance-active", b"false"),
            (b"authorization", b"Bearer real-token"),
        ]
    )
    result = filter_headers(headers)
    assert "x-dms-principal" not in result
    assert "x-dms-roles" not in result
    assert "x-dms-maintenance-active" not in result
    assert result == {"authorization": "Bearer real-token"}


def test_filter_headers_case_insensitive_for_x_dms_prefix():
    headers = Headers(raw=[(b"X-Dms-Principal", b"spoofed")])
    assert filter_headers(headers) == {}
