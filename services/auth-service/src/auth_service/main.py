import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from urllib.parse import urlparse

from dms_auth_client import TokenValidator, make_current_user_dependency
from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth_service import (
    admin_users,
    directory_federation,
    federation_crypto,
    keycloak_client,
    superuser,
)
from auth_service.admin_users import UserAlreadyExistsError, UserNotFoundError, build_admin_client
from auth_service.bootstrap import DOMAIN_ADMIN_ACCOUNTS, ensure_realm_and_client
from auth_service.consumer import start_consuming
from auth_service.directory_federation import CONTACT_DIRECTORY_CAPABILITY
from auth_service.federation_hub_client import FederationHubClient
from auth_service.keycloak_client import InvalidCredentialsError
from auth_service.models import Base, FederationIdentity, SsoConfig
from auth_service.permission_client import PermissionServiceClient
from auth_service.schemas import (
    DirectoryEntryOut,
    DirectoryFederationStatusOut,
    DirectorySearchRequest,
    FederatedDirectoryEntryOut,
    LoginRequest,
    LogoutRequest,
    OidcAuthorizeOut,
    OidcCallbackRequest,
    RealmRoleOut,
    RealmRolesRequest,
    RefreshRequest,
    SsoConfigIn,
    SsoConfigOut,
    SuperuserStatus,
    ThemePreference,
    TokenResponse,
    UserCreate,
    UserLookupOut,
    UserOut,
)
from auth_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

_FEDERATION_IDENTITY_ID = 1
_SSO_CONFIG_ID = 1


async def _superuser_poll_loop() -> None:
    """Erzwingt die Zeitbefristung der Break-Glass-Aktivierung (4.6) - gleiches
    Poll- statt Push-Prinzip wie workflow-services SLA-Zeitüberwachung
    (ADR 0020), hier auf ein Keycloak-Attribut statt eine DB-Zeile angewandt."""
    while True:
        try:
            if superuser.deactivate_if_expired(app.state.keycloak_admin):
                await publish_event(
                    "auth.superuser.deactivated",
                    {"reason": "expired"},
                    actor="system:superuser-poll",
                )
        except Exception:
            logger.exception(
                "Superuser-Poll-Tick fehlgeschlagen - wird beim nächsten Tick erneut versucht."
            )
        await asyncio.sleep(settings.superuser_poll_interval_seconds)


async def _ensure_federation_identity(
    session_factory,
) -> FederationHubClient | None:
    """Einmalige Selbstregistrierung am Federation Hub für die föderierte
    Kontaktsuche (2.5/7.4, P15-S4) - opt-in, bleibt `None` ohne konfigurierten
    `settings.federation_hub_base_url`. Gleiches Muster wie `workflow_service.
    main._ensure_federation_identity` (ADR 0028/0039), hier bewusst eine
    eigene, von workflow-services Registrierung unabhängige Installation im
    selben Adressbuch (siehe `models.FederationIdentity`-Docstring/ADR 0054)."""
    if not settings.federation_hub_base_url:
        return None
    client = FederationHubClient(settings.federation_hub_base_url)
    callback_base_url = f"{settings.installation_gateway_base_url}/api/auth-service"
    async with session_factory() as session:
        identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
        if identity is None:
            private_pem, public_pem = federation_crypto.generate_keypair()
            installation_id = str(uuid.uuid4())
            await client.register(
                installation_id=installation_id,
                private_key_pem=private_pem,
                display_name=f"{settings.installation_display_name} (Kontakte)",
                callback_base_url=callback_base_url,
                public_key_pem=public_pem.decode("utf-8"),
                version="1.0",
                min_compatible_peer_version="1.0",
                supported_process_types=[CONTACT_DIRECTORY_CAPABILITY],
            )
            identity = FederationIdentity(
                id=_FEDERATION_IDENTITY_ID,
                installation_id=installation_id,
                private_key_pem=private_pem,
                public_key_pem=public_pem,
                created_at=datetime.now(UTC),
            )
            session.add(identity)
            await session.commit()
        else:
            try:
                await client.register(
                    installation_id=identity.installation_id,
                    private_key_pem=identity.private_key_pem,
                    display_name=f"{settings.installation_display_name} (Kontakte)",
                    callback_base_url=callback_base_url,
                    public_key_pem=identity.public_key_pem.decode("utf-8"),
                    version="1.0",
                    min_compatible_peer_version="1.0",
                    supported_process_types=[CONTACT_DIRECTORY_CAPABILITY],
                )
            except Exception:
                logger.warning(
                    "federation_hub_reregistration_failed - Kontaktsuche bleibt beim vorherigen "
                    "Registrierungsstand, kein Hard-Dependency dieser Installation.",
                    exc_info=True,
                )
    return client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    ensure_realm_and_client(settings)
    app.state.keycloak_admin = build_admin_client(settings)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)

    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS auth"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.federation_hub_client = await _ensure_federation_identity(app.state.session_factory)

    # Best-Effort (P6-S5, seit P6-S6 für zwei Domänen statt einer): der
    # Permission Service könnte beim eigenen Start noch nicht erreichbar sein
    # - kein Retry-Loop, heilt beim nächsten Neustart (gleiches Prinzip wie
    # SubjectNotFoundError in structure_consumer.py).
    known_users = admin_users.list_users(app.state.keycloak_admin)
    for username, role_name, _last_name in DOMAIN_ADMIN_ACCOUNTS:
        try:
            account_id = next(u["id"] for u in known_users if u["username"] == username)
            await app.state.permission_client.ensure_role_assignment(
                principal_id=account_id, role_name=role_name
            )
        except Exception:
            logger.warning(
                "Rollenzuweisung für %r konnte nicht sichergestellt werden - Permission "
                "Service noch nicht erreichbar? Wird beim nächsten Neustart erneut versucht.",
                username,
                exc_info=True,
            )

    event_bus = NatsEventBusClient(settings.nats_url, stream="auth")
    await event_bus.connect()
    app.state.event_bus = event_bus

    # Erster Konsument dieses Service überhaupt (P6-S5, 4.6): getrennter
    # Client (ensure_stream=False), da auth-service den Stream "permission"
    # nicht selbst besitzt - gleiches Zwei-Client-Prinzip wie document-service
    # seit P6-S4 (ADR 0022).
    consumer_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await consumer_bus.connect()
    app.state.consumer_bus = consumer_bus
    await start_consuming(
        consumer_bus,
        settings.subjects,
        app.state.keycloak_admin,
        activation_minutes=settings.superuser_activation_minutes,
        publish_event=publish_event,
    )

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    superuser_poll_task = asyncio.create_task(_superuser_poll_loop())

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    superuser_poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await superuser_poll_task
    if registration:
        await registration.stop()
    await consumer_bus.close()
    await event_bus.close()
    await app.state.permission_client.close()
    if app.state.federation_hub_client is not None:
        await app.state.federation_hub_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)

_issuer = f"{settings.keycloak_base_url}/realms/{settings.keycloak_realm}"
_validator = TokenValidator(
    issuer=_issuer,
    audience=settings.keycloak_client_id,
    jwks_url=f"{_issuer}/protocol/openid-connect/certs",
)
get_current_user = make_current_user_dependency(_validator)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def publish_event(event_type: str, payload: dict, actor: str | None = None) -> None:
    event = Event(
        event_type=event_type, service_name=settings.service_name, payload=payload, actor=actor
    )
    await app.state.event_bus.publish(event_type, event.to_bytes())


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    x_dms_maintenance_active: str = Header(default="false"),
) -> TokenResponse:
    """Not-Shutdown (4.8, P6-S6): das Gateway broadcastet den Wartungsmodus-
    Status auf jedem durchgelassenen Request per Header, statt dass jeder
    Backend-Service selbst bei `permission-service` pollen muss (siehe ADR
    0024). Ist der Wartungsmodus aktiv, werden neue Logins außer für den
    Superuser abgelehnt - der Superuser-Login funktioniert unabhängig davon
    ohnehin nur, wenn das Konto zuvor per Break-Glass (4.6) aktiviert wurde."""
    maintenance_active = x_dms_maintenance_active.lower() == "true"
    if maintenance_active and payload.username != superuser.SUPERUSER_USERNAME:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Systemweite Notfallsperre aktiv - Login nur für den Superuser möglich",
        )
    try:
        tokens = await keycloak_client.login(settings, payload.username, payload.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return TokenResponse(**tokens)


@app.post("/refresh", response_model=TokenResponse)
async def refresh_token(payload: RefreshRequest) -> TokenResponse:
    try:
        tokens = await keycloak_client.refresh(settings, payload.refresh_token)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return TokenResponse(**tokens)


@app.get("/me")
async def me(user: dict = Depends(get_current_user)) -> dict:
    """Normalisierte Identität aus dem Token (4.4) - die Übersetzung in
    interne DMS-Rollen übernimmt der Permission Service (4.1, P2-S2), nicht
    der Auth Service selbst.
    """
    return {
        "sub": user.get("sub"),
        "username": user.get("preferred_username"),
        "email": user.get("email"),
        "realm_roles": user.get("realm_access", {}).get("roles", []),
    }


@app.get("/me/preferences", response_model=ThemePreference)
def get_my_preferences(user: dict = Depends(get_current_user)) -> ThemePreference:
    """Cross-UI-Theming (8, P4-S6) - Präferenz hängt am Nutzerkonto (Keycloak-
    Attribut), nicht an einer einzelnen Installation/einem einzelnen Browser,
    damit sie geräteübergreifend wirkt (Nutzer-Feedback nach P4-S5)."""
    theme = admin_users.get_theme_preference(app.state.keycloak_admin, user["sub"])
    return ThemePreference(theme=theme)


@app.put("/me/preferences", response_model=ThemePreference)
def update_my_preferences(
    payload: ThemePreference, user: dict = Depends(get_current_user)
) -> ThemePreference:
    admin_users.set_theme_preference(app.state.keycloak_admin, user["sub"], payload.theme)
    return payload


async def _require_user_management(user: dict) -> None:
    """Retrofit (P6-S5, 4.6): Nutzerverwaltung ist seit P4-S3 ungated -
    Domäne "Nutzer-/Rechteverwaltung", durchgesetzt über einen echten
    Permission-Service-Check statt eines `X-DMS-Roles`-Stringvergleichs (die
    Rolle lebt systemeigen in permission-service, nicht in Keycloak)."""
    allowed = await app.state.permission_client.has_permission(user["sub"], "admin.user_management")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Domain-Admin-Rolle 'Nutzer-/Rechteverwaltung'",
        )


@app.get("/users", response_model=list[UserOut])
async def list_users(user: dict = Depends(get_current_user)) -> list[dict]:
    """Nutzerverwaltung für die Admin-UI (8, seit P4-S3) - liest direkt aus
    Keycloak, keine eigene Nutzertabelle (siehe README: Konten sind bereits
    vollständig durch Keycloak abgedeckt)."""
    await _require_user_management(user)
    return admin_users.list_users(app.state.keycloak_admin)


@app.get("/users/lookup", response_model=UserLookupOut)
async def lookup_user(username: str, user: dict = Depends(get_current_user)) -> dict:
    """Exakte Namensauflösung für JEDEN authentifizierten Nutzer (2.5,
    P14-S6) - bewusst OHNE `admin.user_management`-Gate wie `GET /users`
    oben, siehe `admin_users.find_user_by_username`. `user`-Parameter dient
    nur der Authentifizierungsprüfung (`Depends(get_current_user)` lehnt ein
    fehlendes/ungültiges Token bereits selbst ab), wird inhaltlich nicht
    gebraucht."""
    match = admin_users.find_user_by_username(app.state.keycloak_admin, username)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Nutzer {username!r} unbekannt")
    return match


@app.get("/users/directory", response_model=list[DirectoryEntryOut])
async def search_directory(q: str, user: dict = Depends(get_current_user)) -> list[dict]:
    """Verzeichnis zum Auffinden anderer Mitarbeitender (2.5/4.4, P15-S4) -
    lokal, immer verfügbar für jeden authentifizierten Nutzer (kein
    `admin.user_management`-Gate, siehe `admin_users.search_users`)."""
    return admin_users.search_users(app.state.keycloak_admin, q)


@app.get("/users/directory/federation-status", response_model=DirectoryFederationStatusOut)
async def directory_federation_status(
    session: AsyncSession = Depends(get_session),
) -> DirectoryFederationStatusOut:
    """Ob die föderierte Kontaktsuche auf dieser Installation aktiviert ist
    (2.5: "eigene, explizit opt-in konfigurierbare Fähigkeit") - das Frontend
    zeigt die entsprechende UI-Sektion nur, wenn `enabled=true`."""
    if not settings.federated_directory_enabled or app.state.federation_hub_client is None:
        return DirectoryFederationStatusOut(enabled=False, peer_installation_count=0)
    identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
    if identity is None:
        return DirectoryFederationStatusOut(enabled=False, peer_installation_count=0)
    installations = await app.state.federation_hub_client.list_installations()
    peers = directory_federation.eligible_peers(installations, identity.installation_id)
    return DirectoryFederationStatusOut(enabled=True, peer_installation_count=len(peers))


@app.get("/users/directory/federated", response_model=list[FederatedDirectoryEntryOut])
async def search_federated_directory(
    q: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Installationsübergreifende Kontaktsuche (2.5/7.4, P15-S4) - fragt jede
    über den Federation Hub bekannte, für Kontaktsuche freigegebene Peer-
    Installation DIREKT an (nicht über den Hub relayt, siehe ADR 0054). Liefert
    ausschließlich Treffer ANDERER Installationen - das Frontend ruft
    zusätzlich `GET /users/directory` für die lokalen aus."""
    if not settings.federated_directory_enabled or app.state.federation_hub_client is None:
        raise HTTPException(
            status_code=403,
            detail="Föderierte Kontaktsuche ist auf dieser Installation nicht aktiviert",
        )
    identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
    if identity is None:
        raise HTTPException(status_code=503, detail="Federation Hub noch nicht registriert")
    installations = await app.state.federation_hub_client.list_installations()
    return await directory_federation.search_all_peers(installations, identity, q)


@app.post("/users/directory/federated-search-inbound", response_model=list[DirectoryEntryOut])
async def federated_search_inbound(request: Request) -> list[dict]:
    """Empfängt eine signierte Verzeichnis-Suchanfrage einer Peer-Installation
    (2.5/7.4, P15-S4) - bewusst öffentlich (kein `X-DMS-Principal`, analog zu
    `workflow_service.main.federation_inbound`), authentisiert stattdessen
    über `X-Installation-Signature`, verifiziert gegen den beim Hub
    hinterlegten öffentlichen Schlüssel der ANFRAGENDEN Installation (live
    abgerufen, kein lokal gecachter Peer-Schlüsselspeicher - siehe ADR 0054)."""
    if not settings.federated_directory_enabled or app.state.federation_hub_client is None:
        raise HTTPException(
            status_code=403,
            detail="Föderierte Kontaktsuche ist auf dieser Installation nicht aktiviert",
        )
    body = await request.body()
    caller_installation_id = request.headers.get("X-Installation-Id", "")
    signature = request.headers.get("X-Installation-Signature", "")
    installations = await app.state.federation_hub_client.list_installations()
    caller = next((inst for inst in installations if inst["id"] == caller_installation_id), None)
    if (
        caller is None
        or caller.get("revoked_at") is not None
        or CONTACT_DIRECTORY_CAPABILITY not in caller.get("supported_process_types", [])
    ):
        raise HTTPException(
            status_code=401,
            detail="Unbekannte oder nicht für Kontaktsuche freigegebene Installation",
        )
    if not federation_crypto.verify_body(caller["public_key_pem"].encode("utf-8"), body, signature):
        raise HTTPException(status_code=401, detail="Ungültige Installations-Signatur")
    payload = DirectorySearchRequest.model_validate_json(body)
    return admin_users.search_users(app.state.keycloak_admin, payload.query)


@app.get("/users/count")
def count_users() -> dict:
    """Installationsweite Nutzerzahl (9.1 "benannte Accounts"-Modell, seit
    P9-S1) - ungegatet fuer `license-service`s internen Aufruf (kein
    Service hat einen echten Keycloak-Bearer-Token fuer `Depends(get_current_
    user)`, siehe PROGRESS.md-Recherche)."""
    return {"count": len(admin_users.list_users(app.state.keycloak_admin))}


@app.get("/sessions/count")
def count_sessions() -> dict:
    """Installationsweite Anzahl gleichzeitiger Sessions (9.1 "gleichzeitige
    Nutzer"-Modell, seit P9-S1) - fertige Keycloak-Admin-API-Methode, kein
    neues Session-Tracking noetig. Ungegatet, gleiche Begruendung wie
    `/users/count`."""
    stats = app.state.keycloak_admin.get_client_sessions_stats()
    for entry in stats:
        if entry.get("clientId") == settings.keycloak_client_id:
            return {"count": int(entry.get("active", 0))}
    return {"count": 0}


@app.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, user: dict = Depends(get_current_user)) -> dict:
    await _require_user_management(user)
    try:
        return admin_users.create_user(
            app.state.keycloak_admin,
            username=payload.username,
            email=payload.email,
            password=payload.password,
            first_name=payload.first_name,
            last_name=payload.last_name,
        )
    except UserAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@app.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, user: dict = Depends(get_current_user)) -> None:
    await _require_user_management(user)
    try:
        admin_users.delete_user(app.state.keycloak_admin, user_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


# Keycloak legt diese Realm-Rollen selbst an (Default-Verhalten seit jeher) -
# eine Paket-getriebene Liste (14.1/P17-S1) soll sie nicht als "eigene"
# DMS-Rolle mit auflisten.
_KEYCLOAK_BUILTIN_REALM_ROLE_PREFIXES = ("default-roles-",)
_KEYCLOAK_BUILTIN_REALM_ROLE_NAMES = {"offline_access", "uma_authorization"}


def _is_builtin_realm_role(name: str) -> bool:
    return name in _KEYCLOAK_BUILTIN_REALM_ROLE_NAMES or name.startswith(
        _KEYCLOAK_BUILTIN_REALM_ROLE_PREFIXES
    )


async def _require_service_user_management(x_dms_principal: str) -> None:
    """Wie `_require_user_management` oben, aber für Service-zu-Service-Aufrufe
    ohne Keycloak-JWT (`X-DMS-Principal`-Header statt `Depends(get_current_user)`)
    - identisches Muster wie `workflow_service.main._require_object_config`.
    Genutzt von `config-service`s Konfigurationspaket-Import (14.1, P17-S1),
    das im eigenen Namen (`X-DMS-Principal: config-service`) neue Realm-Rollen
    anlegen können muss."""
    allowed = bool(x_dms_principal) and await app.state.permission_client.has_permission(
        x_dms_principal, "admin.user_management"
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Domain-Admin-Rolle 'Nutzer-/Rechteverwaltung'",
        )


@app.get("/realm-roles", response_model=list[RealmRoleOut])
def list_realm_roles() -> list[dict]:
    """Aktuell existierende Keycloak-Realm-Rollen (14.1, P17-S1) - Grundlage
    für den Export-Zweig eines Konfigurationspakets (`config-service`s
    `realm_roles`-Kategorie). Ungegatet wie `GET /users/count` u. ä. - liefert
    nur Namen, keine sensiblen Daten, identisches Vertrauensmodell wie
    `permission-service`s `GET /roles`, das ebenfalls ungegatet ist."""
    roles = app.state.keycloak_admin.get_realm_roles()
    return [{"name": role["name"]} for role in roles if not _is_builtin_realm_role(role["name"])]


@app.post("/realm-roles", status_code=status.HTTP_204_NO_CONTENT)
async def ensure_realm_roles(
    payload: RealmRolesRequest, x_dms_principal: str = Header(default="")
) -> None:
    """Legt die übergebenen Realm-Rollen idempotent an (14.1, P17-S1) -
    identisches Primitiv wie `bootstrap._ensure_dms_admin_role`
    (`create_realm_role(..., skip_exists=True)`), hier für beliebige, von
    einem Konfigurationspaket vorgegebene Namen (z. B. `dms-poststelle`, 2.5)
    statt fest codiert für `dms-admin`. Legt nur die Rolle an, weist sie
    niemandem zu - Zuweisung bleibt wie bisher außerhalb dieses Service über
    die Keycloak Admin Console (siehe `bootstrap.py`)."""
    await _require_service_user_management(x_dms_principal)
    for name in payload.names:
        app.state.keycloak_admin.create_realm_role(payload={"name": name}, skip_exists=True)


@app.get("/superuser/status", response_model=SuperuserStatus)
def get_superuser_status() -> SuperuserStatus:
    """Break-Glass-Status (4.6) für die Admin-UI-Banner-Anzeige - Aktivierung
    selbst läuft über den bereits bestehenden generischen Approval-Flow des
    Permission Service (`POST /approval-requests` mit
    `action_type="auth.superuser.activate"`), nicht über einen Endpunkt hier."""
    try:
        active, expires_at = superuser.get_status(app.state.keycloak_admin)
    except superuser.SuperuserNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SuperuserStatus(
        active=active,
        expires_at=expires_at.isoformat() if expires_at else None,
        principal_id=superuser.get_principal_id(app.state.keycloak_admin),
    )


@app.post("/superuser/deactivate", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_superuser() -> None:
    """Vorzeitiges, freiwilliges Beenden der Aktivierung (4.6) - ergänzt den
    automatischen Ablauf über den Poll-Loop."""
    try:
        superuser.deactivate(app.state.keycloak_admin)
    except superuser.SuperuserNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    # Kein authentifizierter Aufrufer-Kontext an diesem Endpunkt - das
    # deaktivierte Superuser-Konto selbst ist die naechstbeste Angabe (es
    # gibt nur eines, siehe get_superuser_status).
    await publish_event(
        "auth.superuser.deactivated",
        {"reason": "manual"},
        actor=superuser.get_principal_id(app.state.keycloak_admin),
    )


# --- SSO/automatischer Login (Post-Roadmap-Feature, Kerberos/SPNEGO über
# Keycloak) --------------------------------------------------------------


async def _get_or_create_sso_config(session: AsyncSession) -> SsoConfig:
    config = await session.get(SsoConfig, _SSO_CONFIG_ID)
    if config is None:
        config = SsoConfig(id=_SSO_CONFIG_ID, enabled=False, updated_at=datetime.now(UTC))
        session.add(config)
        await session.flush()
    return config


def _redirect_uri_origin_allowed(redirect_uri: str) -> bool:
    """Open-Redirect-Absicherung für `GET /oidc/authorize`/`POST /oidc/
    callback` - `redirect_uri` kommt vom Client, muss also gegen eine feste
    Allow-Liste geprüft werden, exakt gleiches Prinzip wie gateway-services
    `cors_allowed_origins`."""
    parsed = urlparse(redirect_uri)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin in settings.sso_redirect_uri_allowed_origins


@app.get("/oidc/authorize", response_model=OidcAuthorizeOut)
async def oidc_authorize(redirect_uri: str, state: str) -> OidcAuthorizeOut:
    """SSO/automatischer Login - der Login-Einstiegspunkt selbst, daher
    öffentlich (kein DMS-Token existiert an dieser Stelle noch). Liefert nur
    die URL zurück, der Client navigiert selbst dorthin (kein serverseitiger
    Redirect, konsistent mit dem übrigen Service-Stil dieses Projekts)."""
    if not _redirect_uri_origin_allowed(redirect_uri):
        raise HTTPException(status_code=400, detail="redirect_uri nicht erlaubt")
    state_param = state or str(uuid.uuid4())
    return OidcAuthorizeOut(
        authorization_url=keycloak_client.authorization_url(
            settings, redirect_uri=redirect_uri, state=state_param
        )
    )


@app.post("/oidc/callback", response_model=TokenResponse)
async def oidc_callback(
    payload: OidcCallbackRequest, x_dms_maintenance_active: str = Header(default="false")
) -> TokenResponse:
    """Tauscht den von Keycloaks Redirect gelieferten `code` serverseitig
    gegen Tokens - identisches Antwortformat wie `POST /login`, damit sich am
    Frontend-Token-Speichermechanismus nichts ändern muss. Öffentlich wie
    `/oidc/authorize` (der Aufrufer hat noch kein DMS-Token).

    Gleiche Not-Shutdown-Sperre wie `POST /login` (4.8) - dort wird VOR dem
    eigentlichen Login geprüft, hier erst DANACH (der Benutzername ist vor
    dem Code-Austausch nicht bekannt): ist der Wartungsmodus aktiv und der
    Token gehört nicht dem Superuser, werden die frisch ausgestellten Tokens
    verworfen statt zurückgegeben - sonst würde SSO die Sperre umgehen, die
    der Formular-Login bereits durchsetzt."""
    if not _redirect_uri_origin_allowed(payload.redirect_uri):
        raise HTTPException(status_code=400, detail="redirect_uri nicht erlaubt")
    try:
        tokens = await keycloak_client.exchange_code(
            settings, code=payload.code, redirect_uri=payload.redirect_uri
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    if x_dms_maintenance_active.lower() == "true":
        claims = _validator.validate(tokens["access_token"])
        if claims.get("preferred_username") != superuser.SUPERUSER_USERNAME:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Systemweite Notfallsperre aktiv - Login nur für den Superuser möglich",
            )
    return TokenResponse(**tokens)


@app.get("/sso-config", response_model=SsoConfigOut)
async def get_sso_config(session: AsyncSession = Depends(get_session)) -> SsoConfigOut:
    """Ungegatet wie `GET /share-link-config` bei document-service - reine
    Information, ob SSO aktiv ist, keine sensiblen Daten. `login/page.tsx`
    fragt dies VOR dem Anzeigen des Passwort-Formulars ab."""
    config = await _get_or_create_sso_config(session)
    await session.commit()
    return config


@app.put("/sso-config", response_model=SsoConfigOut)
async def put_sso_config(
    payload: SsoConfigIn,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SsoConfigOut:
    """Anders als `GET` gegated - ein An-/Ausschalten von SSO ist
    sicherheitsrelevant (`admin.user_management`, gleiche Domäne wie
    Nutzerverwaltung selbst)."""
    await _require_user_management(user)
    config = await _get_or_create_sso_config(session)
    config.enabled = payload.enabled
    config.updated_at = datetime.now(UTC)
    await session.commit()
    return config


@app.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: LogoutRequest) -> None:
    """Beendet die Sitzung wirklich auf Keycloak-Seite (siehe
    `keycloak_client.end_session`-Docstring) - ohne diesen Endpunkt gab es
    bislang GAR KEINEN Logout-Mechanismus, "Abmelden" löschte nur lokale
    Tokens. Best-effort aus Sicht des Frontends (`auth-context.tsx`s
    `logout()` blockiert den lokalen Logout nicht bei einem Fehler hier)."""
    await keycloak_client.end_session(settings, payload.refresh_token)
