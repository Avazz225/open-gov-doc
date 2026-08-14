import asyncio

from virus_scan_service.engines.interface import ScanEngine, ScanVerdict


class ClamdEngine(ScanEngine):
    """Production-ready engine against a separately operated `clamd` daemon
    via its INSTREAM protocol (length-prefixed TCP chunks) - no external
    client package needed, the protocol is simple enough for a direct
    `asyncio` socket implementation.

    Not the default engine in this development environment (see
    `EicarSignatureEngine`/ADR 0010): on first start, `clamd` downloads its
    signature database via `freshclam`, which takes minutes and requires
    internet access to the ClamAV mirrors - not reliable enough for a
    reproducible `docker compose up` in this environment. Can be activated
    via `DMS_SCAN_ENGINE=clamd` against a self-hosted `clamd`.
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
        # Response format per ClamAV docs: "stream: OK" or
        # "stream: <signature name> FOUND".
        if text.endswith("OK"):
            return ScanVerdict(clean=True)
        if "FOUND" in text:
            threat_name = text.split(":", 1)[1].strip().removesuffix("FOUND").strip()
            return ScanVerdict(clean=False, threat_name=threat_name)
        raise RuntimeError(f"Unerwartete clamd-Antwort: {text!r}")
