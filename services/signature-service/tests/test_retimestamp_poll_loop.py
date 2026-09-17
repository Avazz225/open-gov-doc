import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from dms_db_base import make_session_factory
from signature_service import main, repository
from signature_service.connectors import build_connectors
from signature_service.connectors.interface import SignerInfo
from signature_service.document_client import DocumentServiceClient

DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")

# A cutoff in the future makes every already-signed signature "due"
# immediately, without mutating `main.settings` (a module-level singleton
# every `TestClient`-started app instance's own real, ambient
# `_retimestamp_poll_loop` also reads - see `main._run_retimestamp_tick`'s
# docstring for why that global must stay untouched by tests).
_DUE_CUTOFF = datetime.now(UTC) + timedelta(days=1)
_NOT_DUE_CUTOFF = datetime.now(UTC) - timedelta(days=365)


async def test_run_retimestamp_tick_extends_a_due_signature_and_advances_its_version(
    engine, pdf_document
):
    """`main._run_retimestamp_tick` (PAdES-B-LTA, 3.10, Post-Roadmap Phase
    41 Session 1) is deliberately not exposed as a manual HTTP endpoint
    (same "no manual trigger" precedent as document-service's retention
    poll loop) - exercised directly here instead of through `TestClient`,
    since `TestClient` runs the app's own lifespan-bound resources
    (engine/document_client) on a separate event loop, and cross-loop
    asyncpg use would break. This test builds its own connectors/
    document_client bound to THIS test's event loop instead, sharing only
    the already-persisted DB state via the `engine` fixture."""
    document_id, source_version = pdf_document
    session_factory = make_session_factory(engine)
    document_client = DocumentServiceClient(DOCUMENT_SERVICE_URL)
    try:
        async with session_factory() as setup_session:
            ca = await repository.get_or_create_ca(setup_session)
            tsa = await repository.get_or_create_tsa(
                setup_session,
                ca_certificate_pem=ca.certificate_pem,
                ca_private_key_pem=ca.private_key_pem,
            )
            await setup_session.commit()
            connectors = build_connectors(
                main.settings,
                ca_certificate_pem=ca.certificate_pem,
                ca_private_key_pem=ca.private_key_pem,
                tsa_certificate_pem=tsa.certificate_pem,
                tsa_private_key_pem=tsa.private_key_pem,
            )
            connector = connectors["internal"]

            _content_type, pdf_bytes = await document_client.get_version_content(
                document_id, source_version
            )
            signer = SignerInfo(
                principal_id="retimestamp-test-signer",
                display_name="Retimestamp Test",
                email="retimestamp-test@example.com",
            )
            signed = await connector.sign(pdf_bytes, signer=signer, level="ses")
            checkin = await document_client.checkin_signed_version(
                document_id,
                expected_base_version_number=source_version,
                signed_bytes=signed.signed_pdf_bytes,
                filename="signed.pdf",
                created_by=signer.display_name,
                comment="Testsignatur (Retimestamp-Vorbereitung)",
            )
            signature = await repository.create_signature(
                setup_session,
                document_id=document_id,
                source_version_number=source_version,
                version_number=checkin["version"]["version_number"],
                level="ses",
                connector_id="internal",
                signer_principal_id=signer.principal_id,
                signer_display_name=signer.display_name,
                certificate_subject=signed.certificate_subject,
                certificate_serial=signed.certificate_serial,
                certificate_not_before=signed.certificate_not_before,
                certificate_not_after=signed.certificate_not_after,
                reason=None,
            )
            await setup_session.commit()
            signature_id = signature.id
            version_before_retimestamp = signature.version_number

        fake_app = SimpleNamespace(
            state=SimpleNamespace(
                session_factory=session_factory,
                connectors=connectors,
                document_client=document_client,
            )
        )

        await main._run_retimestamp_tick(fake_app, cutoff=_DUE_CUTOFF)

        async with session_factory() as verify_session:
            updated = await repository.get_signature(verify_session, signature_id)
            assert updated.last_timestamped_at is not None
            assert updated.version_number == version_before_retimestamp + 1
    finally:
        await document_client.close()


async def test_run_retimestamp_tick_skips_signatures_not_yet_due(engine, pdf_document):
    """The default interval (365 days, `Settings.retimestamp_interval_days`)
    must NOT pick up a signature created moments ago."""
    document_id, source_version = pdf_document
    session_factory = make_session_factory(engine)
    document_client = DocumentServiceClient(DOCUMENT_SERVICE_URL)
    try:
        async with session_factory() as setup_session:
            ca = await repository.get_or_create_ca(setup_session)
            tsa = await repository.get_or_create_tsa(
                setup_session,
                ca_certificate_pem=ca.certificate_pem,
                ca_private_key_pem=ca.private_key_pem,
            )
            await setup_session.commit()
            connectors = build_connectors(
                main.settings,
                ca_certificate_pem=ca.certificate_pem,
                ca_private_key_pem=ca.private_key_pem,
                tsa_certificate_pem=tsa.certificate_pem,
                tsa_private_key_pem=tsa.private_key_pem,
            )
            connector = connectors["internal"]

            _content_type, pdf_bytes = await document_client.get_version_content(
                document_id, source_version
            )
            signer = SignerInfo(
                principal_id="retimestamp-test-signer-2",
                display_name="Retimestamp Test 2",
                email="retimestamp-test-2@example.com",
            )
            signed = await connector.sign(pdf_bytes, signer=signer, level="ses")
            checkin = await document_client.checkin_signed_version(
                document_id,
                expected_base_version_number=source_version,
                signed_bytes=signed.signed_pdf_bytes,
                filename="signed.pdf",
                created_by=signer.display_name,
                comment="Testsignatur (nicht faellig)",
            )
            signature = await repository.create_signature(
                setup_session,
                document_id=document_id,
                source_version_number=source_version,
                version_number=checkin["version"]["version_number"],
                level="ses",
                connector_id="internal",
                signer_principal_id=signer.principal_id,
                signer_display_name=signer.display_name,
                certificate_subject=signed.certificate_subject,
                certificate_serial=signed.certificate_serial,
                certificate_not_before=signed.certificate_not_before,
                certificate_not_after=signed.certificate_not_after,
                reason=None,
            )
            await setup_session.commit()
            signature_id = signature.id
            version_before = signature.version_number

        fake_app = SimpleNamespace(
            state=SimpleNamespace(
                session_factory=session_factory,
                connectors=connectors,
                document_client=document_client,
            )
        )

        await main._run_retimestamp_tick(fake_app, cutoff=_NOT_DUE_CUTOFF)

        async with session_factory() as verify_session:
            unchanged = await repository.get_signature(verify_session, signature_id)
            assert unchanged.last_timestamped_at is None
            assert unchanged.version_number == version_before
    finally:
        await document_client.close()
