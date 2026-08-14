from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("federation")


class HubIdentity(Base):
    """Eigenes Signaturschlüsselpaar des Hub (RSA-2048, `cryptography`, gleiche
    Konvention wie `signature-service`s interne CA, ADR 0025) - bewusst eine
    einzelne Zeile mit fester ``id=1``, gleiches Singleton-Muster wie
    `InternalCa`. Der Hub signiert damit jede an eine Installation zugestellte
    Nachricht (``X-Federation-Hub-Signature``), sodass die empfangende
    Installation echt verifizieren kann, dass die Zustellung tatsächlich vom
    Hub stammt - ohne dass irgendwo ein geteiltes Geheimnis im Klartext
    gespeichert werden müsste (siehe ADR 0028).

    ``ca_certificate_pem`` (seit Post-Roadmap Phase 21 Session 2, ADR 0085) -
    dasselbe Schlüsselpaar zusätzlich als selbstsigniertes X.509-Root-CA-
    Zertifikat verpackt (analog `signature-service`s `InternalCa`, ADR 0025) -
    KEIN separates Schlüsselpaar, nur eine zusätzliche Zertifikatshülle um
    denselben privaten Schlüssel. Damit kann der Hub jeder Installation ein
    von ihm signiertes, zeitlich begrenztes Zertifikat ausstellen
    (`Installation.certificate_pem`), statt nur einen rohen, unbegrenzt
    gültigen öffentlichen Schlüssel zu speichern. Bewusst KEIN echtes
    Transport-mTLS (siehe ADR 0039 "Kein Ort für eine echte PKI in diesem
    Projekt" - dessen Begründung unverändert gilt, kein Service dieses Repos
    terminiert TLS selbst) - die Zertifikatsprüfung passiert weiterhin auf
    Anwendungsebene, zusätzlich zur bestehenden Signaturprüfung."""

    __tablename__ = "hub_identity"

    id: Mapped[int] = mapped_column(primary_key=True)
    private_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    public_key_pem: Mapped[bytes] = mapped_column(LargeBinary)
    ca_certificate_pem: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Installation(Base):
    """Ein Eintrag im Adressbuch (7.4) - eine bei diesem Hub angemeldete,
    vollständig unabhängige Installation. ``id`` ist die von der Installation
    selbst gewählte öffentliche Kennung (nicht vom Hub vergeben).
    ``public_key_pem`` ist der öffentliche Schlüssel, mit dem **andere**
    Installationen für diese hier bestimmte Payloads verschlüsseln (Ende-zu-
    Ende, der Hub selbst besitzt nie den privaten Schlüssel dazu) - seit
    P13-S4 (ADR 0039) dient derselbe Schlüssel zusätzlich als kryptografische
    Identität dieser Installation: jede schreibende Anfrage an den Hub muss
    mit dem passenden privaten Schlüssel signiert sein (ersetzt das zuvor
    verwendete ``api_key_hash``-Feld, ein reines geteiltes Geheimnis). Ein
    Schlüsselwechsel läuft ausschließlich über ``POST
    /installations/{id}/rotate-key`` (signiert mit dem noch aktuellen
    Schlüssel) - eine reguläre Re-Registrierung überschreibt ``public_key_pem``
    nicht mehr stillschweigend. ``revoked_at``/``revoked_reason`` erlauben
    einem Hub-Betreiber, eine kompromittierte Installation sofort zu sperren
    (``POST /installations/{id}/revoke``), unabhängig davon, ob die
    Installation selbst noch signieren kann.

    ``certificate_pem`` (seit Post-Roadmap Phase 21 Session 2, ADR 0085) - ein
    vom Hub ausgestelltes, zeitlich begrenztes X.509-Zertifikat, das
    ``public_key_pem`` bindet (Certificate-Pinning-Äquivalent: die
    Installation UND jeder Dritte kann anhand der Hub-CA prüfen, dass genau
    dieser Schlüssel zu genau dieser ``id`` gehört, mit einer klaren
    Gültigkeitsgrenze statt unbegrenzter Gültigkeit). `authenticate_signed_
    request` verifiziert bei jeder Anfrage die vollständige Kette bis zur
    Hub-CA UND das Gültigkeitsfenster erneut aus den Zertifikats-Bytes selbst
    (`crypto_utils.verify_installation_certificate`) - ``certificate_not_after``
    ist nur ein daraus abgeleiteter, denormalisierter Anzeigewert (Admin-UI/
    Migrationserkennung), keine eigenständige Sicherheitsprüfung. Beide Felder
    werden bei Registrierung und bei jeder Schlüsselrotation neu gesetzt (der
    alte Schlüssel würde sonst ein Zertifikat für einen nicht mehr aktuellen
    Schlüssel behalten). ``NULL`` bei Installationen, die vor dieser Session
    registriert wurden UND noch nicht rotiert/neu ausgestellt wurden -
    `authenticate_signed_request` überspringt die Zertifikatsprüfung in
    diesem Fall (Bestandsschutz), verlangt aber weiterhin die bereits
    bestehende Signaturprüfung."""

    __tablename__ = "installation"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256))
    callback_base_url: Mapped[str] = mapped_column(String(512))
    public_key_pem: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(32))
    min_compatible_peer_version: Mapped[str] = mapped_column(String(32))
    supported_process_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    supported_document_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    certificate_pem: Mapped[str | None] = mapped_column(Text, nullable=True)
    certificate_not_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Handover(Base):
    """Metadaten einer einzelnen Übergabe-Vermittlung (7.4: "protokolliert nur
    Metadaten des Vermittlungsvorgangs ... nicht die Dokumentinhalte selbst") -
    bewusst **kein** Feld für den (Ende-zu-Ende verschlüsselten) Payload selbst,
    der wird synchron weitergeleitet, nie hier persistiert. Seit Post-Roadmap
    Phase 20 Session 5 (ADR 0081) gilt das weiterhin - ein zwischenzeitlich
    per Retry erneut zuzustellender Payload wird nur FLÜCHTIG im Prozess-
    speicher gehalten (`app.state.pending_handover_payloads`), nie in dieser
    Tabelle - ein Neustart des Hub während eines offenen Retry-Fensters
    verliert daher die Möglichkeit zur automatischen Nachzustellung (siehe
    docs/services/federation-hub-service.md "Offene Punkte")."""

    __tablename__ = "handover"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    from_installation_id: Mapped[str] = mapped_column(String(128), index=True)
    to_installation_id: Mapped[str] = mapped_column(String(128), index=True)
    process_type: Mapped[str] = mapped_column(String(256))
    # "pending" -> "delivered"|"pending_retry"->...->"delivery_failed" ->
    # "completed"|"result_delivery_failed" (Post-Roadmap Phase 20 Session 5,
    # ADR 0081: "pending_retry" ist neu, "delivery_failed" wird erst nach
    # Erschoepfung von max_handover_delivery_attempts erreicht statt wie
    # zuvor sofort bei jedem einzelnen Fehlschlag).
    status: Mapped[str] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
