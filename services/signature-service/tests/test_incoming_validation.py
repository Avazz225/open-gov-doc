"""PAdES validation of incoming PDFs (P92-S1, ADR 0248) against a generated
test PKI. Pure: no database, no other service. A local aiohttp server plays
the CRL distribution point - and the internal target of an SSRF attempt."""

import os
import socket

import pytest
import testpki
from aiohttp import web
from aiohttp.test_utils import TestServer
from signature_service.connectors.interface import SignerInfo
from signature_service.connectors.internal import (
    InternalSelfSignedConnector,
    generate_root_ca,
    issue_tsa_certificate,
)
from signature_service.validation.fetcher import (
    FetchBlockedError,
    _check_url,
    build_fenced_session,
    is_blocked_address,
)
from signature_service.validation.interface import (
    FetchLimits,
    TrustAnchorInput,
    ValidationPolicy,
)
from signature_service.validation.pades import PadesValidator
from signature_service.validation.trust import InvalidAnchorError, der_of, parse_anchor_pem

with open(os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf"), "rb") as _f:
    PDF = _f.read()


def _policy(
    anchors: list[bytes],
    *,
    mode: str = "soft-fail",
    fetching: bool = False,
    allow_private: bool = False,
    max_signatures: int = 20,
    max_path_length: int = 5,
    internal: list[bytes] | None = None,
) -> ValidationPolicy:
    return ValidationPolicy(
        anchors=[TrustAnchorInput(der, "installation") for der in anchors]
        + [TrustAnchorInput(der, "internal") for der in (internal or [])],
        revocation_mode=mode,
        outbound_fetching=fetching,
        max_signatures=max_signatures,
        max_path_length=max_path_length,
        time_limit_seconds=30,
        fetch=FetchLimits(per_request_timeout_seconds=3, allow_local_test_server=allow_private),
    )


@pytest.fixture
async def crl_server():
    """Serves `/crl` from `state["crl"]` and counts every request."""
    state = {"crl": b"", "hits": 0}

    async def crl(_request):
        state["hits"] += 1
        return web.Response(body=state["crl"], content_type="application/pkix-crl")

    async def anything(_request):
        state["hits"] += 1
        return web.Response(text="internal secret")

    app = web.Application()
    app.router.add_get("/crl", crl)
    app.router.add_route("*", "/{tail:.*}", anything)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    state["base"] = f"http://127.0.0.1:{server.port}"
    yield state
    await server.close()


validator = PadesValidator()


def test_applies_to_pdfs_by_type_or_name():
    assert validator.applies("application/pdf", None)
    assert validator.applies("application/pdf; charset=binary", "x")
    assert validator.applies(None, "Bescheid.PDF")
    assert not validator.applies("image/png", "scan.png")
    assert not validator.applies(None, None)


async def test_valid_signature_from_an_installation_anchor_passes():
    root = testpki.make_cert("Partnerbehörde Root", ca=True)
    leaf = testpki.make_cert("Erika Mustermann", root, ca=False)
    signed = await testpki.sign_pdf(PDF, leaf, [root])

    outcome = await validator.validate(signed, _policy([root.der]))

    [finding] = outcome.findings
    assert finding.indication == "TOTAL-PASSED"
    assert finding.kind == "signature"
    assert finding.qualification == "ades"
    assert finding.trust_source == "installation"
    assert finding.coverage == "entire_document"
    assert "Erika Mustermann" in finding.signer_subject
    assert finding.failure_reason is None


async def test_a_path_through_an_intermediate_passes_and_respects_the_length_limit():
    root = testpki.make_cert("Root", ca=True)
    intermediate = testpki.make_cert("Intermediate", root, ca=True)
    leaf = testpki.make_cert("Signer", intermediate, ca=False)
    signed = await testpki.sign_pdf(PDF, leaf, [intermediate, root])

    assert (await validator.validate(signed, _policy([root.der]))).findings[
        0
    ].indication == "TOTAL-PASSED"
    limited = await validator.validate(signed, _policy([root.der], max_path_length=0))
    assert limited.findings[0].indication != "TOTAL-PASSED"


async def test_unknown_ca_is_indeterminate_not_failed():
    root = testpki.make_cert("Unbekannte CA", ca=True)
    leaf = testpki.make_cert("Jemand", root, ca=False)
    signed = await testpki.sign_pdf(PDF, leaf, [root])

    [finding] = (await validator.validate(signed, _policy([]))).findings

    assert finding.indication == "INDETERMINATE"
    assert finding.sub_indication == "NO_CERTIFICATE_CHAIN_FOUND"
    assert finding.failure_reason == "Aussteller ist nicht als vertrauenswürdig hinterlegt"
    assert finding.trust_source is None
    # A certificate embedded in the file never becomes an anchor.
    assert finding.qualification == "unknown"


async def test_changed_bytes_fail_the_hash():
    root = testpki.make_cert("Root", ca=True)
    leaf = testpki.make_cert("Signer", root, ca=False)
    signed = await testpki.sign_pdf(PDF, leaf, [root])

    [finding] = (
        await validator.validate(testpki.flip_signed_byte(signed), _policy([root.der]))
    ).findings

    assert finding.indication == "TOTAL-FAILED"
    assert finding.sub_indication == "HASH_FAILURE"


async def test_shadow_attack_style_incremental_update_fails():
    """Content swapped by an incremental update after signing: the signature
    still verifies over its own revision, yet the result must be FAILED."""
    root = testpki.make_cert("Root", ca=True)
    leaf = testpki.make_cert("Signer", root, ca=False)
    signed = await testpki.sign_pdf(PDF, leaf, [root])

    [finding] = (
        await validator.validate(testpki.replace_page_content(signed), _policy([root.der]))
    ).findings

    assert finding.indication == "TOTAL-FAILED"
    assert finding.sub_indication == "MODIFIED_AFTER_SIGNING"
    assert finding.coverage == "changed_after_signing"
    assert finding.modification_level == "OTHER"


async def test_a_second_signature_is_a_permitted_change_for_the_first():
    root = testpki.make_cert("Root", ca=True)
    first = testpki.make_cert("Erste", root, ca=False)
    second = testpki.make_cert("Zweite", root, ca=False)
    once = await testpki.sign_pdf(PDF, first, [root], field_name="Sig1")
    twice = await testpki.sign_pdf(once, second, [root], field_name="Sig2")

    findings = (await validator.validate(twice, _policy([root.der]))).findings

    assert [f.position for f in findings] == ["Sig1", "Sig2"]
    assert [f.indication for f in findings] == ["TOTAL-PASSED", "TOTAL-PASSED"]
    assert findings[0].coverage == "permitted_changes"
    assert findings[1].coverage == "entire_document"


async def test_more_signatures_than_the_limit_are_truncated():
    root = testpki.make_cert("Root", ca=True)
    leaf = testpki.make_cert("Signer", root, ca=False)
    once = await testpki.sign_pdf(PDF, leaf, [root], field_name="Sig1")
    twice = await testpki.sign_pdf(once, leaf, [root], field_name="Sig2")

    outcome = await validator.validate(twice, _policy([root.der], max_signatures=1))

    assert outcome.truncated
    assert len(outcome.findings) == 1


async def test_revoked_certificate_does_not_pass(crl_server):
    root = testpki.make_cert("Root", ca=True)
    leaf = testpki.make_cert("Gesperrt", root, ca=False, crl_url=f"{crl_server['base']}/crl")
    crl_server["crl"] = testpki.make_crl(root, [leaf])
    signed = await testpki.sign_pdf(PDF, leaf, [root])

    [finding] = (
        await validator.validate(signed, _policy([root.der], fetching=True, allow_private=True))
    ).findings

    assert crl_server["hits"] >= 1
    assert finding.indication != "TOTAL-PASSED"
    assert finding.sub_indication.startswith("REVOKED")
    assert "gesperrt" in finding.failure_reason


async def test_unreachable_revocation_soft_fail_passes_hard_fail_does_not():
    root = testpki.make_cert("Root", ca=True)
    # Port 9 (discard) on a public documentation address: never answers.
    leaf = testpki.make_cert("Signer", root, ca=False, crl_url="http://192.0.2.1/crl")
    signed = await testpki.sign_pdf(PDF, leaf, [root])

    soft = (await validator.validate(signed, _policy([root.der]))).findings[0]
    hard = (await validator.validate(signed, _policy([root.der], mode="hard-fail"))).findings[0]

    assert soft.indication == "TOTAL-PASSED"
    assert hard.indication == "INDETERMINATE"


async def test_ssrf_attempt_to_an_internal_address_is_never_fetched(crl_server):
    """The CRL URL in the uploaded certificate points at an internal address.
    With fetching on, the fence must refuse it: no request reaches the
    server, and the result can't pass in hard-fail mode."""
    root = testpki.make_cert("Root", ca=True)
    leaf = testpki.make_cert(
        "Angreifer",
        root,
        ca=False,
        crl_url=f"{crl_server['base']}/admin/secret",
        aia_url=f"{crl_server['base']}/aia",
    )
    signed = await testpki.sign_pdf(PDF, leaf, [root])

    [finding] = (
        await validator.validate(signed, _policy([root.der], mode="hard-fail", fetching=True))
    ).findings

    assert crl_server["hits"] == 0
    assert finding.indication == "INDETERMINATE"


async def test_own_signatures_pass_against_the_internal_ca():
    ca_pem, ca_key = generate_root_ca()
    tsa_pem, tsa_key = issue_tsa_certificate(ca_pem, ca_key)
    connector = InternalSelfSignedConnector(ca_pem, ca_key, tsa_pem, tsa_key)
    signed = await connector.sign(
        PDF,
        signer=SignerInfo(principal_id="max", display_name="Max Muster", email="max@example.org"),
        level="aes",
    )
    ca_der = der_of(parse_anchor_pem(ca_pem))

    findings = (
        await validator.validate(signed.signed_pdf_bytes, _policy([], internal=[ca_der]))
    ).findings

    signature = next(f for f in findings if f.kind == "signature")
    assert signature.indication == "TOTAL-PASSED"
    assert signature.trust_source == "internal"
    assert signature.best_signature_time is not None  # B-LTA: proven by the timestamp
    timestamps = [f for f in findings if f.kind == "document_timestamp"]
    assert timestamps and all(t.indication == "TOTAL-PASSED" for t in timestamps)


async def test_a_file_the_strict_reader_refuses_is_not_checkable():
    broken = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF"

    [finding] = (await validator.validate(broken, _policy([]))).findings

    assert finding.kind == "file"
    assert finding.indication == "INDETERMINATE"
    assert finding.sub_indication == "STRUCTURE_NOT_CHECKABLE"
    assert finding.failure_reason == "Dateistruktur nicht prüfbar"


async def test_an_unsigned_pdf_has_no_findings():
    assert (await validator.validate(PDF, _policy([]))).findings == []


# --- the fence on its own ---------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "::1",
        "fd00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "0.0.0.0",
    ],
)
def test_internal_addresses_are_blocked(address):
    assert is_blocked_address(address, FetchLimits())


def test_public_addresses_and_configured_cluster_ranges():
    assert not is_blocked_address("93.184.216.34", FetchLimits())
    assert is_blocked_address(
        "93.184.216.34", FetchLimits(extra_blocked_networks=["93.184.216.0/24"])
    )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.org/crl",
        "file:///etc/passwd",
        "http://example.org:8080/crl",
        "http://127.0.0.1/crl",
        "http://[::1]/crl",
        "http://169.254.169.254/latest/meta-data",
    ],
)
def test_disallowed_urls_are_refused(url):
    with pytest.raises(FetchBlockedError):
        _check_url(url, FetchLimits())


def test_allowed_urls_pass_the_url_check():
    _check_url("http://crl.example.org/ca.crl", FetchLimits())
    _check_url("https://ocsp.example.org", FetchLimits())


async def test_a_name_resolving_to_an_internal_address_is_refused(crl_server):
    # A name on the standard port passes the URL check; the resolver must
    # refuse it because "localhost" resolves to loopback.
    assert socket.gethostbyname("localhost").startswith("127.")
    session = build_fenced_session(FetchLimits(per_request_timeout_seconds=2))
    try:
        with pytest.raises(FetchBlockedError):
            async with session.get("http://localhost/crl"):
                pass
    finally:
        await session.close()
    assert crl_server["hits"] == 0


async def test_redirect_to_another_host_and_oversized_bodies_are_refused():
    async def redirect(_request):
        raise web.HTTPFound("http://other.example.org/x")

    async def big(_request):
        return web.Response(body=b"x" * 4096)

    app = web.Application()
    app.router.add_get("/redirect", redirect)
    app.router.add_get("/big", big)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    limits = FetchLimits(allow_local_test_server=True, max_response_bytes=1024)
    session = build_fenced_session(limits)
    try:
        base = f"http://127.0.0.1:{server.port}"
        with pytest.raises(FetchBlockedError):
            async with session.get(f"{base}/redirect"):
                pass
        with pytest.raises(FetchBlockedError):
            async with session.get(f"{base}/big") as response:
                await response.read()
    finally:
        await session.close()
        await server.close()


def test_uploaded_anchor_must_be_a_ca_certificate():
    root = testpki.make_cert("Root", ca=True)
    leaf = testpki.make_cert("Leaf", root, ca=False)
    assert parse_anchor_pem(root.pem).subject == root.cert.subject
    with pytest.raises(InvalidAnchorError):
        parse_anchor_pem(leaf.pem)
    with pytest.raises(InvalidAnchorError):
        parse_anchor_pem(b"not a certificate")
