from virus_scan_service.engines.interface import ScanEngine, ScanVerdict

# Die standardisierte EICAR-Testdatei-Signatur (https://www.eicar.org/) - von
# praktisch jedem echten Antivirus-Produkt zu Integrationstestzwecken erkannt.
# Aufgeteilt über eine String-Konkatenation, damit dieselbe Datei nicht selbst
# von einem auf dem Build-Host laufenden Virenscanner fälschlich als Fund
# markiert wird.
EICAR_SIGNATURE = (
    r"X5O!P%@AP[4\PZX54(P^)7CC)7}$" "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
).encode("ascii")


class EicarSignatureEngine(ScanEngine):
    """Standard-Engine dieses Grundgerüsts (ADR 0010): erkennt ausschließlich
    die genormte EICAR-Testsignatur - eine echte, branchenübliche
    Signaturprüfung (kein reiner "immer sauber"-Stub), aber ohne die
    Notwendigkeit einer laufend zu aktualisierenden vollständigen
    Virendefinitionsdatenbank. Erkennt keine echte Schadsoftware - für den
    produktiven Einsatz ist die austauschbare `ClamdEngine` vorgesehen."""

    async def scan(self, data: bytes) -> ScanVerdict:
        if EICAR_SIGNATURE in data:
            return ScanVerdict(clean=False, threat_name="Eicar-Test-Signature")
        return ScanVerdict(clean=True)
