"""CAdES validation (P92-S4, ADR 0248) with the generated test PKI: enveloping
`.p7m` and detached `.p7s`, several signers, PEM/base64 encodings, content
extraction. Pure: no database, no other service."""

import base64

import pytest
import testpki
import testtl
from signature_service.validation import trusted_lists
from signature_service.validation.cades import CadesValidator, extract_content
from signature_service.validation.interface import (
    FetchLimits,
    TrustAnchorInput,
    ValidationPolicy,
)

CONTENT = b"Bescheid vom 24.11.2026 - Inhalt, der signiert wurde."
validator = CadesValidator()


def _policy(anchors: list[bytes], registry=None) -> ValidationPolicy:
    return ValidationPolicy(
        anchors=[TrustAnchorInput(der, "installation") for der in anchors],
        revocation_mode="soft-fail",
        outbound_fetching=False,
        max_signatures=20,
        max_path_length=5,
        time_limit_seconds=30,
        fetch=FetchLimits(),
        tsp_registry=registry,
    )


@pytest.fixture(scope="module")
def pki():
    root = testpki.make_cert("Partner Root", ca=True)
    return {"root": root, "leaf": testpki.make_cert("Max Mustermann", root, ca=False)}


def test_applies_by_type_or_name():
    assert validator.applies("application/pkcs7-mime", None)
    assert validator.applies("application/pkcs7-signature", "x")
    assert validator.applies("text/plain", "bescheid.pdf.p7m")
    assert validator.applies(None, "anlage.docx.P7S")
    assert not validator.applies("application/pdf", "a.pdf")


async def test_enveloping_signature_passes_and_its_content_comes_out(pki):
    p7m = testpki.cms_sign(CONTENT, [pki["leaf"]], [pki["root"]], detached=False)

    [finding] = (await validator.validate(p7m, _policy([pki["root"].der]))).findings

    assert finding.format == "cades"
    assert finding.position == "enveloping"
    assert finding.indication == "TOTAL-PASSED"
    assert finding.trust_source == "installation"
    assert finding.coverage == "entire_document"
    assert "Max Mustermann" in finding.signer_subject
    assert extract_content(p7m) == CONTENT


@pytest.mark.parametrize("encoding", ["pem", "base64"])
async def test_pem_and_bare_base64_are_read(pki, encoding):
    der = testpki.cms_sign(CONTENT, [pki["leaf"]], [pki["root"]], detached=False)
    data = (
        testpki.cms_sign(CONTENT, [pki["leaf"]], [pki["root"]], detached=False, pem=True)
        if encoding == "pem"
        else base64.b64encode(der)
    )
    [finding] = (await validator.validate(data, _policy([pki["root"].der]))).findings
    assert finding.indication == "TOTAL-PASSED"
    assert extract_content(data) == CONTENT


async def test_detached_signature_against_its_document(pki):
    p7s = testpki.cms_sign(CONTENT, [pki["leaf"]], [pki["root"]], detached=True)
    policy = _policy([pki["root"].der])

    [ok] = (await validator.validate_detached(p7s, CONTENT, policy, position="anlage.p7s")).findings
    assert ok.indication == "TOTAL-PASSED" and ok.position == "anlage.p7s"

    [bad] = (
        await validator.validate_detached(p7s, CONTENT + b" (geaendert)", policy, position="x")
    ).findings
    assert bad.indication == "TOTAL-FAILED"
    assert bad.sub_indication == "HASH_FAILURE"
    assert bad.coverage == "changed_after_signing"
    assert extract_content(p7s) is None


async def test_detached_signature_without_its_document_is_not_checkable(pki):
    p7s = testpki.cms_sign(CONTENT, [pki["leaf"]], [pki["root"]], detached=True)
    [finding] = (await validator.validate(p7s, _policy([pki["root"].der]))).findings
    assert finding.indication == "INDETERMINATE"
    assert finding.sub_indication == "SIGNED_DATA_NOT_FOUND"


async def test_parallel_signers_are_validated_one_by_one(pki):
    stranger_root = testpki.make_cert("Fremde CA", ca=True)
    stranger = testpki.make_cert("Unbekannt", stranger_root, ca=False)
    p7m = testpki.cms_sign(
        CONTENT, [pki["leaf"], stranger], [pki["root"], stranger_root], detached=False
    )

    findings = (await validator.validate(p7m, _policy([pki["root"].der]))).findings

    # DER sorts the signers' SET, so the order isn't the signing order.
    assert sorted(f.position for f in findings) == ["enveloping#1", "enveloping#2"]
    by_signer = {f.signer_subject.split(": ")[-1]: f.indication for f in findings}
    assert by_signer == {"Max Mustermann": "TOTAL-PASSED", "Unbekannt": "INDETERMINATE"}


async def test_not_a_signature_is_not_checkable():
    [finding] = (await validator.validate(b"kein CMS", _policy([]))).findings
    assert finding.kind == "file"
    assert finding.sub_indication == "STRUCTURE_NOT_CHECKABLE"


async def test_qualified_signer_via_eu_lists_is_qes():
    tl_signer = testtl.make_tlso("DE TL signer")
    ca = testpki.make_cert("QTSP CA", ca=True)
    registry, _ = trusted_lists.build_registry([testtl.make_tl(qualified_ca=ca, tlso=tl_signer)])
    leaf = testtl.make_qualified_leaf("Erika QES", ca)
    p7m = testpki.cms_sign(CONTENT, [leaf], [ca], detached=False)

    [finding] = (await validator.validate(p7m, _policy([], registry))).findings

    assert finding.qualification == "qes"
    assert finding.trust_source == "eu_trusted_list"
