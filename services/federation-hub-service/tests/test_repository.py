import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from federation_hub_service import crypto_utils, repository
from federation_hub_service.crypto_utils import sign_body
from federation_hub_service.schemas import InstallationRegister


def _generate_keypair() -> tuple[bytes, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


def make_payload(*, public_key_pem: str, **overrides) -> InstallationRegister:
    data = {
        "id": f"install-{uuid.uuid4().hex[:8]}",
        "display_name": "Test-Installation",
        "callback_base_url": "http://example.test",
        "public_key_pem": public_key_pem,
        "version": "1.0",
        "min_compatible_peer_version": "1.0",
    }
    data.update(overrides)
    return InstallationRegister(**data)


def _raw_body(payload: InstallationRegister) -> bytes:
    return json.dumps(payload.model_dump()).encode("utf-8")


async def register(session, private_key_pem: bytes, payload: InstallationRegister):
    body = _raw_body(payload)
    return await repository.register_or_update_installation(
        session, payload, raw_body=body, presented_signature=sign_body(private_key_pem, body)
    )


async def test_register_new_installation_requires_matching_signature(session):
    private_pem, public_pem = _generate_keypair()
    other_private_pem, _ = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    body = _raw_body(payload)

    with pytest.raises(repository.UnauthorizedError):
        await repository.register_or_update_installation(
            session, payload, raw_body=body, presented_signature=sign_body(other_private_pem, body)
        )

    installation = await register(session, private_pem, payload)
    assert installation.id == payload.id


async def test_register_existing_installation_without_signature_rejected(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)

    body = _raw_body(payload)
    with pytest.raises(repository.UnauthorizedError):
        await repository.register_or_update_installation(
            session, payload, raw_body=body, presented_signature=None
        )


async def test_register_existing_installation_with_correct_signature_updates(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)

    updated_payload = make_payload(
        id=payload.id, public_key_pem=public_pem, display_name="Neuer Name"
    )
    installation = await register(session, private_pem, updated_payload)
    assert installation.display_name == "Neuer Name"


async def test_register_update_ignores_submitted_public_key_change(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)

    _, other_public_pem = _generate_keypair()
    updated_payload = make_payload(id=payload.id, public_key_pem=other_public_pem)
    installation = await register(session, private_pem, updated_payload)
    assert installation.public_key_pem == public_pem


async def test_register_rejects_revoked_installation(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)
    await repository.revoke_installation(session, payload.id, reason="test")

    body = _raw_body(payload)
    with pytest.raises(repository.UnauthorizedError):
        await repository.register_or_update_installation(
            session, payload, raw_body=body, presented_signature=sign_body(private_pem, body)
        )


async def test_deregister_requires_matching_signature(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)

    with pytest.raises(repository.UnauthorizedError):
        await repository.deregister_installation(
            session, payload.id, presented_signature=sign_body(private_pem, b"wrong-bytes")
        )

    await repository.deregister_installation(
        session, payload.id, presented_signature=sign_body(private_pem, payload.id.encode("utf-8"))
    )

    installations = await repository.list_installations(session)
    assert payload.id not in [i.id for i in installations]


async def test_rotate_installation_key_requires_current_signature(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)

    new_private_pem, new_public_pem = _generate_keypair()
    rotate_body = json.dumps({"new_public_key_pem": new_public_pem}).encode("utf-8")

    with pytest.raises(repository.UnauthorizedError):
        await repository.rotate_installation_key(
            session,
            payload.id,
            raw_body=rotate_body,
            new_public_key_pem=new_public_pem,
            presented_signature=sign_body(new_private_pem, rotate_body),
        )

    installation = await repository.rotate_installation_key(
        session,
        payload.id,
        raw_body=rotate_body,
        new_public_key_pem=new_public_pem,
        presented_signature=sign_body(private_pem, rotate_body),
    )
    assert installation.public_key_pem == new_public_pem


async def test_revoke_installation_sets_timestamp_and_reason(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)

    installation = await repository.revoke_installation(
        session, payload.id, reason="Schlüssel kompromittiert"
    )
    assert installation.revoked_at is not None
    assert installation.revoked_reason == "Schlüssel kompromittiert"


async def test_authenticate_signed_request(session):
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    await register(session, private_pem, payload)

    body = b"some-request-body"
    installation = await repository.authenticate_signed_request(
        session, installation_id=payload.id, body=body, signature=sign_body(private_pem, body)
    )
    assert installation.id == payload.id

    with pytest.raises(repository.UnauthorizedError):
        await repository.authenticate_signed_request(
            session, installation_id=payload.id, body=body, signature="not-a-valid-signature"
        )

    with pytest.raises(repository.UnauthorizedError):
        await repository.authenticate_signed_request(
            session, installation_id="does-not-exist", body=body, signature="anything"
        )


@pytest.mark.parametrize(
    ("a_version", "a_min_peer", "b_version", "b_min_peer", "expected"),
    [
        ("1.0", "1.0", "1.0", "1.0", True),
        ("2.3", "1.0", "1.5", "1.0", True),
        ("1.0", "2.0", "1.5", "1.0", False),
        ("2.0", "1.0", "1.5", "3.0", False),
    ],
)
async def test_is_version_compatible(
    session, a_version, a_min_peer, b_version, b_min_peer, expected
):
    private_a, public_a = _generate_keypair()
    private_b, public_b = _generate_keypair()
    payload_a = make_payload(
        public_key_pem=public_a, version=a_version, min_compatible_peer_version=a_min_peer
    )
    payload_b = make_payload(
        public_key_pem=public_b, version=b_version, min_compatible_peer_version=b_min_peer
    )
    installation_a = await register(session, private_a, payload_a)
    installation_b = await register(session, private_b, payload_b)

    assert repository.is_version_compatible(installation_a, installation_b) is expected


async def test_hub_identity_singleton(session):
    first = await repository.get_or_create_hub_identity(session)
    second = await repository.get_or_create_hub_identity(session)

    assert first.private_key_pem == second.private_key_pem
    assert first.public_key_pem == second.public_key_pem


# --- Zertifikatsebene (Post-Roadmap Phase 21 Session 2, ADR 0085) ----------


async def test_hub_identity_has_a_self_signed_ca_certificate(session):
    identity = await repository.get_or_create_hub_identity(session)

    assert identity.ca_certificate_pem is not None
    ca_cert = x509.load_pem_x509_certificate(identity.ca_certificate_pem)
    # Selbstsigniert: Aussteller == Inhaber, und die Signatur ist mit dem
    # EIGENEN öffentlichen Schlüssel (aus demselben Schlüsselpaar) prüfbar.
    assert ca_cert.issuer == ca_cert.subject
    ca_cert.verify_directly_issued_by(ca_cert)


async def test_hub_identity_ca_certificate_survives_repeated_calls(session):
    first = await repository.get_or_create_hub_identity(session)
    second = await repository.get_or_create_hub_identity(session)

    assert first.ca_certificate_pem == second.ca_certificate_pem


async def test_register_installation_issues_a_hub_signed_certificate(session):
    """Der reine `register_or_update_installation`-Aufruf selbst stellt (wie
    `create_handover`) kein Zertifikat aus - das passiert erst in
    `main.register_installation`. Dieser Test ruft die Ausstellung explizit
    auf, wie es der Endpunkt tut."""
    identity = await repository.get_or_create_hub_identity(session)
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    installation = await register(session, private_pem, payload)

    await repository.issue_or_renew_installation_certificate(
        session,
        installation,
        ca_certificate_pem=identity.ca_certificate_pem,
        ca_private_key_pem=identity.private_key_pem,
    )

    assert installation.certificate_pem is not None
    assert installation.certificate_not_after is not None
    assert installation.certificate_not_after > datetime.now(UTC)
    cert = x509.load_pem_x509_certificate(installation.certificate_pem.encode("utf-8"))
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == payload.id
    assert crypto_utils.verify_installation_certificate(
        identity.ca_certificate_pem,
        installation.certificate_pem,
        installation_id=payload.id,
        installation_public_key_pem=public_pem,
    )


async def test_verify_installation_certificate_rejects_a_certificate_not_issued_by_the_hub(
    session,
):
    """Ein Zertifikat, das eine Installation sich selbst ausstellt (statt es
    vom Hub zu bekommen), darf NICHT als gültig durchgehen - sonst wäre die
    ganze Zertifikatsebene wirkungslos."""
    identity = await repository.get_or_create_hub_identity(session)
    other_private_pem, other_public_pem = _generate_keypair()
    other_key = serialization.load_pem_private_key(other_private_pem, password=None)
    self_signed = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "impostor")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "impostor")]))
        .public_key(other_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=5))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .sign(other_key, hashes.SHA256())
    )
    forged_cert_pem = self_signed.public_bytes(serialization.Encoding.PEM).decode("utf-8")

    assert not crypto_utils.verify_installation_certificate(
        identity.ca_certificate_pem,
        forged_cert_pem,
        installation_id="impostor",
        installation_public_key_pem=other_public_pem,
    )


async def test_verify_installation_certificate_rejects_an_expired_certificate(session):
    identity = await repository.get_or_create_hub_identity(session)
    _, installation_public_pem = _generate_keypair()
    ca_cert = x509.load_pem_x509_certificate(identity.ca_certificate_pem)
    ca_private_key = serialization.load_pem_private_key(identity.private_key_pem, password=None)
    installation_public_key = serialization.load_pem_public_key(
        installation_public_pem.encode("utf-8")
    )
    now = datetime.now(UTC)
    expired = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expired-install")]))
        .issuer_name(ca_cert.subject)
        .public_key(installation_public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=400))
        .not_valid_after(now - timedelta(days=1))
        .sign(ca_private_key, hashes.SHA256())
    )
    expired_cert_pem = expired.public_bytes(serialization.Encoding.PEM).decode("utf-8")

    assert not crypto_utils.verify_installation_certificate(
        identity.ca_certificate_pem,
        expired_cert_pem,
        installation_id="expired-install",
        installation_public_key_pem=installation_public_pem,
    )


async def test_rotate_key_reissues_a_certificate_bound_to_the_new_key(session):
    identity = await repository.get_or_create_hub_identity(session)
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    installation = await register(session, private_pem, payload)
    await repository.issue_or_renew_installation_certificate(
        session,
        installation,
        ca_certificate_pem=identity.ca_certificate_pem,
        ca_private_key_pem=identity.private_key_pem,
    )
    original_certificate_pem = installation.certificate_pem

    new_private_pem, new_public_pem = _generate_keypair()
    rotate_body = json.dumps({"new_public_key_pem": new_public_pem}).encode("utf-8")
    rotated = await repository.rotate_installation_key(
        session,
        payload.id,
        raw_body=rotate_body,
        new_public_key_pem=new_public_pem,
        presented_signature=sign_body(private_pem, rotate_body),
    )
    await repository.issue_or_renew_installation_certificate(
        session,
        rotated,
        ca_certificate_pem=identity.ca_certificate_pem,
        ca_private_key_pem=identity.private_key_pem,
    )

    assert rotated.certificate_pem != original_certificate_pem
    new_cert = x509.load_pem_x509_certificate(rotated.certificate_pem.encode("utf-8"))
    assert (
        new_cert.public_key().public_numbers()
        == serialization.load_pem_public_key(new_public_pem.encode("utf-8")).public_numbers()
    )


async def test_list_installations_without_certificate(session):
    identity = await repository.get_or_create_hub_identity(session)
    private_with, public_with = _generate_keypair()
    with_cert = await register(session, private_with, make_payload(public_key_pem=public_with))
    await repository.issue_or_renew_installation_certificate(
        session,
        with_cert,
        ca_certificate_pem=identity.ca_certificate_pem,
        ca_private_key_pem=identity.private_key_pem,
    )
    private_without, public_without = _generate_keypair()
    without_cert = await register(
        session, private_without, make_payload(public_key_pem=public_without)
    )

    pending = await repository.list_installations_without_certificate(session)
    pending_ids = {i.id for i in pending}
    assert without_cert.id in pending_ids
    assert with_cert.id not in pending_ids


async def test_authenticate_signed_request_skips_certificate_check_without_one(session):
    """Bestandsschutz (ADR 0085): eine Installation ohne Zertifikat
    authentisiert weiterhin allein über die Signatur - kein Zertifikat wurde
    hier absichtlich nicht ausgestellt (`register()` allein tut das nicht)."""
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    installation = await register(session, private_pem, payload)
    assert installation.certificate_pem is None

    body = b"some-request-body"
    authenticated = await repository.authenticate_signed_request(
        session, installation_id=payload.id, body=body, signature=sign_body(private_pem, body)
    )
    assert authenticated.id == payload.id


async def test_authenticate_signed_request_rejects_an_invalid_certificate(session):
    identity = await repository.get_or_create_hub_identity(session)
    private_pem, public_pem = _generate_keypair()
    payload = make_payload(public_key_pem=public_pem)
    installation = await register(session, private_pem, payload)
    # Absichtlich ein für eine ANDERE Installation ausgestelltes Zertifikat
    # zugewiesen - simuliert eine manipulierte/fehlerhafte Zeile, ohne die
    # Signaturprüfung selbst zu umgehen.
    other_private_pem, other_public_pem = _generate_keypair()
    other_payload = make_payload(public_key_pem=other_public_pem)
    other_installation = await register(session, other_private_pem, other_payload)
    await repository.issue_or_renew_installation_certificate(
        session,
        other_installation,
        ca_certificate_pem=identity.ca_certificate_pem,
        ca_private_key_pem=identity.private_key_pem,
    )
    installation.certificate_pem = other_installation.certificate_pem
    installation.certificate_not_after = other_installation.certificate_not_after
    await session.flush()

    body = b"some-request-body"
    with pytest.raises(repository.UnauthorizedError):
        await repository.authenticate_signed_request(
            session,
            installation_id=payload.id,
            body=body,
            signature=sign_body(private_pem, body),
            hub_ca_certificate_pem=identity.ca_certificate_pem,
        )


# --- Periodic cleanup of terminal-status `handover` rows (P63-S2) ----------


async def _create_handover_with_age(
    session, *, status: str, age_seconds: float, process_type: str = "test-process"
) -> None:
    handover = await repository.create_handover(
        session,
        handover_id=str(uuid.uuid4()),
        from_installation_id="from-installation",
        to_installation_id="to-installation",
        process_type=process_type,
    )
    handover.status = status
    handover.created_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
    await session.flush()


async def test_purge_stale_handovers_removes_old_terminal_rows(session):
    await _create_handover_with_age(session, status="completed", age_seconds=1_000_000)

    deleted = await repository.purge_stale_handovers(session, cleanup_after_seconds=604800.0)

    assert deleted == 1


async def test_purge_stale_handovers_leaves_recent_terminal_rows_alone(session):
    await _create_handover_with_age(session, status="completed", age_seconds=60.0)

    deleted = await repository.purge_stale_handovers(session, cleanup_after_seconds=604800.0)

    assert deleted == 0


async def test_purge_stale_handovers_never_removes_a_non_terminal_row_regardless_of_age(session):
    """A stuck `pending`/`pending_retry`/`delivered`/`result_pending_retry`
    row should never happen in practice, but the cleanup must not silently
    delete evidence of it if it somehow does - only genuinely terminal
    statuses are eligible."""
    for status in ("pending", "delivered", "pending_retry", "result_pending_retry"):
        await _create_handover_with_age(session, status=status, age_seconds=1_000_000)

    deleted = await repository.purge_stale_handovers(session, cleanup_after_seconds=604800.0)

    assert deleted == 0


# --- Persisted retry payload cache (Post-Roadmap Phase 73 Session 3, ADR 0213) ---


async def test_save_get_delete_pending_payload_round_trip(session):
    handover = await repository.create_handover(
        session,
        handover_id=str(uuid.uuid4()),
        from_installation_id="from-installation",
        to_installation_id="to-installation",
        process_type="test-process",
    )
    await session.flush()

    assert await repository.get_pending_payload(session, handover.id, leg="forward") is None

    await repository.save_pending_payload(
        session, handover.id, leg="forward", payload={"encrypted_payload": "opaque"}
    )
    assert await repository.get_pending_payload(session, handover.id, leg="forward") == {
        "encrypted_payload": "opaque"
    }
    # The two legs are independent - writing "forward" must not create or
    # affect a "result" row for the same handover_id.
    assert await repository.get_pending_payload(session, handover.id, leg="result") is None

    await repository.delete_pending_payload(session, handover.id, leg="forward")
    assert await repository.get_pending_payload(session, handover.id, leg="forward") is None
    # Idempotent - deleting an already-absent row is a no-op, not an error
    # (matches the old dict's `.pop(handover.id, None)`).
    await repository.delete_pending_payload(session, handover.id, leg="forward")


async def test_save_pending_payload_overwrites_an_existing_row(session):
    """`submit_handover_result` has no uniqueness guard preventing it from
    running twice for the same `handover_id` before a previous
    `result_pending_retry` attempt resolves (see `save_pending_payload`'s
    own docstring) - the upsert must overwrite, not raise or duplicate."""
    handover = await repository.create_handover(
        session,
        handover_id=str(uuid.uuid4()),
        from_installation_id="from-installation",
        to_installation_id="to-installation",
        process_type="test-process",
    )
    await session.flush()

    await repository.save_pending_payload(
        session, handover.id, leg="result", payload={"encrypted_result": "first"}
    )
    await repository.save_pending_payload(
        session, handover.id, leg="result", payload={"encrypted_result": "second"}
    )

    assert await repository.get_pending_payload(session, handover.id, leg="result") == {
        "encrypted_result": "second"
    }


async def test_count_pending_payloads_counts_only_the_requested_leg(session):
    for leg, count in (("forward", 2), ("result", 1)):
        for _ in range(count):
            handover = await repository.create_handover(
                session,
                handover_id=str(uuid.uuid4()),
                from_installation_id="from-installation",
                to_installation_id="to-installation",
                process_type="test-process",
            )
            await session.flush()
            await repository.save_pending_payload(session, handover.id, leg=leg, payload={})

    assert await repository.count_pending_payloads(session, leg="forward") == 2
    assert await repository.count_pending_payloads(session, leg="result") == 1


async def test_purge_stale_handovers_cascades_to_pending_retry_payload(session):
    """ADR 0213 cascade proof: `handover_retry_payload.handover_id` has
    `ON DELETE CASCADE`, so `purge_stale_handovers`'s existing plain bulk
    `DELETE` (`session.execute(delete(Handover).where(...))`, no ORM object
    loaded per row - see its own docstring) needs zero Python-side change to
    also remove an orphaned payload row; the database does it. Covers
    exactly the `delivery_failed` case where a payload deliberately stays
    cached past exhaustion for a manual `POST .../retry` (see
    `models.HandoverRetryPayload`'s docstring)."""
    await _create_handover_with_age(session, status="delivery_failed", age_seconds=1_000_000)
    handovers = await repository.list_handovers(session, status="delivery_failed")
    handover = handovers[0]
    await repository.save_pending_payload(
        session, handover.id, leg="forward", payload={"encrypted_payload": "opaque"}
    )
    await session.flush()
    assert await repository.get_pending_payload(session, handover.id, leg="forward") is not None

    deleted = await repository.purge_stale_handovers(session, cleanup_after_seconds=604800.0)
    assert deleted == 1

    # Queries the payload table directly - confirms no orphaned row survives
    # the parent `Handover` row's deletion.
    assert await repository.get_pending_payload(session, handover.id, leg="forward") is None
