import pytest
from virus_scan_service.engines import build_engine
from virus_scan_service.engines.clamd_engine import ClamdEngine
from virus_scan_service.engines.eicar_engine import EICAR_SIGNATURE, EicarSignatureEngine
from virus_scan_service.settings import Settings


async def test_eicar_engine_flags_the_standard_test_signature():
    engine = EicarSignatureEngine()

    verdict = await engine.scan(EICAR_SIGNATURE)

    assert verdict.clean is False
    assert verdict.threat_name == "Eicar-Test-Signature"


async def test_eicar_engine_flags_signature_embedded_in_larger_content():
    engine = EicarSignatureEngine()

    verdict = await engine.scan(b"Kopfzeile\n" + EICAR_SIGNATURE + b"\nFusszeile")

    assert verdict.clean is False


async def test_eicar_engine_reports_clean_for_harmless_content():
    engine = EicarSignatureEngine()

    verdict = await engine.scan(b"Hallo Welt, ein ganz normales Dokument.")

    assert verdict.clean is True
    assert verdict.threat_name is None


def test_build_engine_selects_eicar_by_default():
    engine = build_engine(Settings())

    assert isinstance(engine, EicarSignatureEngine)


def test_build_engine_selects_clamd():
    engine = build_engine(Settings(scan_engine="clamd", clamd_host="clamav", clamd_port=3310))

    assert isinstance(engine, ClamdEngine)


def test_build_engine_rejects_unknown_engine():
    with pytest.raises(ValueError, match="Unbekannte Scan-Engine"):
        build_engine(Settings(scan_engine="does-not-exist"))


async def test_clamd_engine_raises_when_daemon_unreachable():
    # Kein clamd in dieser Umgebung verdrahtet (siehe README/ADR 0010) - der
    # Verbindungsfehler muss durchgereicht werden, statt fälschlich "clean" zu
    # melden (fail-closed-Prinzip, siehe document-service-Integration).
    engine = ClamdEngine(host="127.0.0.1", port=1, timeout=1.0)

    with pytest.raises(OSError):
        await engine.scan(b"beliebiger Inhalt")
