from virus_scan_service.engines.clamd_engine import ClamdEngine
from virus_scan_service.engines.eicar_engine import EicarSignatureEngine
from virus_scan_service.engines.interface import ScanEngine, ScanVerdict
from virus_scan_service.settings import Settings


def build_engine(settings: Settings) -> ScanEngine:
    if settings.scan_engine == "eicar":
        return EicarSignatureEngine()
    if settings.scan_engine == "clamd":
        return ClamdEngine(settings.clamd_host, settings.clamd_port, settings.clamd_timeout_seconds)
    raise ValueError(f"Unbekannte Scan-Engine: {settings.scan_engine!r}")


__all__ = ["ClamdEngine", "EicarSignatureEngine", "ScanEngine", "ScanVerdict", "build_engine"]
