from virus_scan_service.engines.interface import ScanEngine, ScanVerdict

# The standardized EICAR test file signature (https://www.eicar.org/) -
# recognized by virtually every real antivirus product for integration
# testing purposes. Split via string concatenation so that this very file
# does not itself get falsely flagged as a hit by a virus scanner running on
# the build host.
EICAR_SIGNATURE = (
    r"X5O!P%@AP[4\PZX54(P^)7CC)7}$" "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
).encode("ascii")


class EicarSignatureEngine(ScanEngine):
    """Default engine of this scaffold (ADR 0010): recognizes exclusively the
    standardized EICAR test signature - a real, industry-standard signature
    check (not a mere "always clean" stub), but without the need for a
    continuously updated, full virus definition database. Does not detect
    actual malware - the swappable `ClamdEngine` is intended for production
    use."""

    async def scan(self, data: bytes) -> ScanVerdict:
        if EICAR_SIGNATURE in data:
            return ScanVerdict(clean=False, threat_name="Eicar-Test-Signature")
        return ScanVerdict(clean=True)
