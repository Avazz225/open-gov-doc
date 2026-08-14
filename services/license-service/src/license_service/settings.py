from dms_common import BaseServiceSettings

# Nur fuer lokale Entwicklung/Tests - der zugehoerige private Schluessel liegt
# NUR in tests/fixtures/dev_private_key.pem, niemals hier oder in einem
# Docker-Image (ADR 0032). Eine echte Installation ueberschreibt diesen Wert
# ueber DMS_LICENSE_PUBLIC_KEY_PEM mit dem vom Lizenzgeber ausgelieferten
# oeffentlichen Schluessel.
_DEV_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAhhFaFeTnxhFYMStNc6Mi
3/Nsn13bHB/XX7ID4Og/xnrEc6tLTob2IB65Igj2bU4TwLo3sjkwzX0rlzBpdH6a
1yHWaS3BFPrkMWDm8OztUoADNTUBaeiQHs/0vQxpZr3uF29RG7eEBUdRzc1nDaR1
G22FGT35/Jou3EotIoAI1oefP9Dxep5KImBfM9AB3rQCMvedAAM/nw3M8jx60GJw
ggHw4gL+Zh6p++CHZvszg7dfTmik9DFuSQPyKDTgxIUVroPcic7jTkYVDJ9lq+Yn
4Pork76eoy9juXphoTqgEVZD3hCT4Z7ISEA1m1QM7LAWz9bD6pciNaP+n9UEL6yb
/C3OUA+r3q7o2HsgcQCtBhysqcgmZVpYYzoGmIITMjiD9qjFwFVo+Hd86SWR+SWu
DLLRmFGX1DGJhBJtQSA2KNM9LDlYp2uaV8sDQNEOGJqi+w8/tcp0y0wW/p9C26Pc
fJrK3zepngG9mGnTcNyIMKRUhKGRJ2Y61P1dHzxS6a2xAgMBAAE=
-----END PUBLIC KEY-----
"""


class Settings(BaseServiceSettings):
    service_name: str = "license-service"

    storage_service_base_url: str = "http://localhost:8005"
    document_service_base_url: str = "http://localhost:8006"
    auth_service_base_url: str = "http://localhost:8003"
    permission_service_base_url: str = "http://localhost:8004"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # ADR 0032: RS256, statisch eingebetteter oeffentlicher Schluessel, kein
    # JWKS in dieser Ausbaustufe.
    license_public_key_pem: str = _DEV_PUBLIC_KEY_PEM

    # Schluesselrotation (seit Post-Roadmap Phase 21 Session 1, ADR 0084):
    # `None` (Default) = keine laufende Rotation. Setzt ein Betreiber diesen
    # Wert auf den VORHERIGEN oeffentlichen Schluessel, waehrend
    # `license_public_key_pem` bereits den NEUEN traegt, bleiben unter dem
    # alten Schluessel signierte, bereits installierte Lizenzen fuer eine
    # Uebergangsfrist weiterhin gueltig (siehe `license_verifier.decode`).
    # Wird nach Abschluss der Uebergangsfrist wieder auf `None` gesetzt.
    license_previous_public_key_pem: str | None = None

    license_permission: str = "admin.license"

    # Konzept 9.2: "prueft laufend, nicht nur beim Start" - Poll-Intervall-
    # Konvention fuer nicht-zeitkritische, datumsbasierte Pruefungen (gleicher
    # Wert wie document-service's retention_poll_interval_seconds/
    # archival-service's archival_poll_interval_seconds).
    license_poll_interval_seconds: int = 3600
    license_expiring_soon_threshold_days: int = 30
