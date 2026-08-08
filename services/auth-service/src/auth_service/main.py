import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from dms_auth_client import TokenValidator, make_current_user_dependency
from dms_common import configure_logging
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, status

from auth_service import admin_users, keycloak_client, superuser
from auth_service.admin_users import UserAlreadyExistsError, UserNotFoundError, build_admin_client
from auth_service.bootstrap import DOMAIN_ADMIN_ACCOUNTS, ensure_realm_and_client
from auth_service.consumer import start_consuming
from auth_service.keycloak_client import InvalidCredentialsError
from auth_service.permission_client import PermissionServiceClient
from auth_service.schemas import (
    LoginRequest,
    RefreshRequest,
    SuperuserStatus,
    ThemePreference,
    TokenResponse,
    UserCreate,
    UserOut,
)
from auth_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    ensure_realm_and_client(settings)
    app.state.keycloak_admin = build_admin_client(settings)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)

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


app = FastAPI(title=settings.service_name, lifespan=lifespan)

_issuer = f"{settings.keycloak_base_url}/realms/{settings.keycloak_realm}"
_validator = TokenValidator(
    issuer=_issuer,
    audience=settings.keycloak_client_id,
    jwks_url=f"{_issuer}/protocol/openid-connect/certs",
)
get_current_user = make_current_user_dependency(_validator)


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
