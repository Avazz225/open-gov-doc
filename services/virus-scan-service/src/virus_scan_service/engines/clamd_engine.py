import asyncio

from virus_scan_service.engines.interface import ScanEngine, ScanVerdict


class ClamdEngine(ScanEngine):
    """Produktiv einsetzbare Engine gegen einen separat betriebenen
    `clamd`-Daemon über dessen INSTREAM-Protokoll (längenpräfixierte
    TCP-Chunks) - kein externes Client-Paket nötig, das Protokoll ist einfach
    genug für eine direkte `asyncio`-Socket-Implementierung.

    In dieser Entwicklungsumgebung nicht die Standard-Engine (siehe
    `EicarSignatureEngine`/ADR 0010): `clamd` lädt beim ersten Start seine
    Signaturdatenbank über `freshclam` nach, was Minuten dauert und
    Internetzugriff auf die ClamAV-Mirrors voraussetzt - für einen
    reproduzierbaren `docker compose up` in dieser Umgebung nicht verlässlich
    genug. Über `DMS_SCAN_ENGINE=clamd` gegen einen selbst betriebenen
    `clamd` aktivierbar.
    """

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout

    async def scan(self, data: bytes) -> ScanVerdict:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), timeout=self._timeout
        )
        try:
            writer.write(b"zINSTREAM\0")
            chunk_size = 8192
            for offset in range(0, len(data), chunk_size):
                chunk = data[offset : offset + chunk_size]
                writer.write(len(chunk).to_bytes(4, "big") + chunk)
            writer.write((0).to_bytes(4, "big"))
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=self._timeout)
        finally:
            writer.close()
            await writer.wait_closed()

        text = response.decode("utf-8", errors="replace").strip()
        # Antwortformat lt. ClamAV-Doku: "stream: OK" oder
        # "stream: <Signaturname> FOUND".
        if text.endswith("OK"):
            return ScanVerdict(clean=True)
        if "FOUND" in text:
            threat_name = text.split(":", 1)[1].strip().removesuffix("FOUND").strip()
            return ScanVerdict(clean=False, threat_name=threat_name)
        raise RuntimeError(f"Unerwartete clamd-Antwort: {text!r}")
