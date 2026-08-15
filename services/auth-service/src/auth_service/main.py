import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from urllib.parse import urlparse

from dms_auth_client import (
    InvalidTokenError,
    MultiIssuerTokenValidator,
    TokenValidator,
    make_current_user_dependency,
)
from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_metrics_client import (
    SensorConfigClient,
    bootstrap_http_sensors,
    http_sensor_declarations,
    metrics_payload,
)
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from auth_service import (
    ad_group_mapping,
    admin_users,
    directory_federation,
    domain_admins,
    federation_crypto,
    keycloak_client,
    local_token_issuer,
    superuser,
)
from auth_service.admin_users import UserAlreadyExistsError, UserNotFoundError, build_admin_client
from auth_service.bootstrap import DOMAIN_ADMIN_ACCOUNTS, ensure_realm_and_client
from auth_service.consumer import start_consuming
from auth_service.directory_federation import CONTACT_DIRECTORY_CAPABILITY
from auth_service.federation_hub_client import FederationHubClient
from auth_service.keycloak_client import InvalidCredentialsError
from auth_service.models import (
    AdGroupRoleMapping,
    Base,
    FederationIdentity,
    SsoConfig,
    TechnicalAccount,
)
from auth_service.permission_client import PermissionServiceClient
from auth_service.schemas import (
    AdGroupRoleMappingIn,
    AdGroupRoleMappingOut,
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
    """Enforces the time limit of the break-glass activation (4.6) - same
    poll-instead-of-push principle as workflow-service's SLA time
    monitoring (ADR 0020), applied to a `technical_account` row instead of
    a Keycloak attribute since Phase 18 (ADR 0063)."""
    while True:
        try:
            if await superuser.deactivate_if_expired(app.state.session_factory):
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
    """One-time self-registration with the Federation Hub for the federated
    contact directory search (2.5/7.4, P15-S4) - opt-in, stays `None`
    without a configured `settings.federation_hub_base_url`. Same pattern
    as `workflow_service.main._ensure_federation_identity` (ADR 0028/0039),
    deliberately a separate installation in the same address book here,
    independent of workflow-service's registration (see
    `models.FederationIdentity` docstring/ADR 0054)."""
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

    # Auth decoupling from Keycloak (post-roadmap feature, Phase 18, ADR 0063):
    # the local signing key needs DB access, so it's only available from here
    # on (after the engine setup above) - `_LazyValidator` (below) delays the
    # actual access to `app.state.combined_validator` until the first real
    # request, so that `get_current_user` can still exist as a finished
    # dependency already at module import time.
    signing_key = await local_token_issuer.ensure_signing_key(app.state.session_factory)
    app.state.local_signing_key = signing_key
    app.state.combined_validator = MultiIssuerTokenValidator(
        [
            _keycloak_validator,
            TokenValidator(
                issuer=local_token_issuer.LOCAL_ISSUER,
                audience=settings.keycloak_client_id,
                jwks=local_token_issuer.build_jwks(signing_key.public_key_pem, signing_key.kid),
            ),
        ]
    )

    # Auth decoupling from Keycloak (Phase 18, ADR 0063): the superuser has
    # lived as a `TechnicalAccount` row instead of a Keycloak account since
    # this session - created idempotently right here instead of in
    # `bootstrap.ensure_realm_and_client` (that function is synchronous and
    # purely Keycloak-focused, DB access fits structurally better next to
    # the signing key above).
    await superuser.ensure_superuser_account(app.state.session_factory)

    # Auth decoupling from Keycloak (Phase 18, ADR 0065): domain admin
    # accounts have also lived as `TechnicalAccount` rows instead of
    # Keycloak accounts since this session - created right here, next to
    # the superuser above, instead of in `bootstrap.ensure_realm_and_client`
    # (purely Keycloak-focused).
    for username, role_name in DOMAIN_ADMIN_ACCOUNTS:
        await domain_admins.ensure_domain_admin_account(
            app.state.session_factory, username=username, role_name=role_name
        )

    # Best-effort (P6-S5, for two domains instead of one since P6-S6): the
    # Permission Service might not yet be reachable at its own startup - no
    # retry loop, self-heals on the next restart (same principle as
    # SubjectNotFoundError in structure_consumer.py).
    for username, role_name in DOMAIN_ADMIN_ACCOUNTS:
        try:
            account_id = await domain_admins.get_technical_account_id(
                app.state.session_factory, username
            )
            if account_id is None:
                raise RuntimeError(f"Technisches Konto {username!r} wurde nicht angelegt")
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

    # First consumer of this service ever (P6-S5, 4.6): a separate client
    # (ensure_stream=False), since auth-service does not itself own the
    # "permission" stream - same two-client principle as document-service
    # since P6-S4 (ADR 0022).
    consumer_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await consumer_bus.connect()
    app.state.consumer_bus = consumer_bus
    await start_consuming(
        consumer_bus,
        settings.subjects,
        app.state.session_factory,
        activation_minutes=settings.superuser_activation_minutes,
        publish_event=publish_event,
    )

    # Sensor concept (10.1, full rollout): a fresh `SensorConfigClient` per
    # startup, bound into the module-level `sensor_config_proxy` - not a
    # module-level client itself (see `SensorConfigProxy`'s docstring: its
    # httpx client can't outlive the event loop it was first used on).
    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
        sensors=http_sensor_declarations(),
    )

    superuser_poll_task = asyncio.create_task(_superuser_poll_loop())

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    sensor_config_proxy.unbind()
    await app.state.sensor_config_client.stop()
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

# Sensor concept (10.1, full rollout): must run at module level, right
# after `app` is constructed - see bootstrap_http_sensors's docstring
# for why this can't move into `lifespan` (FastAPI forbids adding
# middleware once the app has started).
sensor_config_proxy, sensor_registry, _http_requests_sensor, _http_duration_sensor = (
    bootstrap_http_sensors(app, settings.service_name)
)

_issuer = f"{settings.keycloak_base_url}/realms/{settings.keycloak_realm}"
_keycloak_validator = TokenValidator(
    issuer=_issuer,
    audience=settings.keycloak_client_id,
    jwks_url=f"{_issuer}/protocol/openid-connect/certs",
)


class _LazyValidator:
    """Auth decoupling (Phase 18): `app.state.combined_validator` only
    exists after lifespan startup (needs DB access for the local signing
    key, see `lifespan()`) - this wrapper delays the actual access until
    the first real request. `make_current_user_dependency` itself still
    gets an immediately available object with `.validate()` as usual
    (pure duck typing)."""

    def validate(self, token: str) -> dict:
        return app.state.combined_validator.validate(token)


get_current_user = make_current_user_dependency(_LazyValidator())


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


@app.get("/metrics")
def get_metrics() -> Response:
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


@app.get("/.well-known/jwks.json")
def get_local_jwks() -> dict:
    """Auth decoupling from Keycloak (post-roadmap feature, Phase 18, ADR
    0063) - JWKS for tokens of technical accounts (superuser/domain admins),
    analogous to Keycloak's `/protocol/openid-connect/certs`. Ungated like
    any JWKS endpoint (public key, no sensitive data)."""
    return local_token_issuer.build_jwks(
        app.state.local_signing_key.public_key_pem, app.state.local_signing_key.kid
    )


def _mint_technical_account_tokens(account: TechnicalAccount) -> TokenResponse:
    """Auth decoupling from Keycloak (Phase 18, ADR 0063) - builds the same
    `TokenResponse` shape as a Keycloak login, just with locally signed
    tokens. `role_name` (if set, e.g. for future domain admins) is carried
    as the sole role in `realm_access.roles` - the superuser itself has no
    role (`role_name=None`), its special privileges run through a direct
    name comparison (`SUPERUSER_USERNAME`) at several points in the system,
    not through RBAC."""
    signing_key = app.state.local_signing_key
    roles = [account.role_name] if account.role_name else []
    access_token = local_token_issuer.mint_token(
        private_key_pem=signing_key.private_key_pem,
        kid=signing_key.kid,
        audience=settings.keycloak_client_id,
        subject=str(account.id),
        username=account.username,
        roles=roles,
        expires_in_seconds=settings.local_access_token_ttl_seconds,
    )
    refresh_token = local_token_issuer.mint_token(
        private_key_pem=signing_key.private_key_pem,
        kid=signing_key.kid,
        audience=settings.keycloak_client_id,
        subject=str(account.id),
        username=account.username,
        roles=roles,
        expires_in_seconds=settings.local_refresh_token_ttl_seconds,
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.local_access_token_ttl_seconds,
        token_type="bearer",
    )


async def _login_technical_account(account: TechnicalAccount, password: str) -> TokenResponse:
    # Deliberately the same generic error message for a wrong password AND
    # for a (not yet) activated/expired account - distinguishable messages
    # would reveal whether an account exists or is merely currently
    # disabled (identical principle to Keycloak's own, equally opaque error
    # message for a disabled account).
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungültige Anmeldedaten"
    )
    if not local_token_issuer.verify_password(password, account.password_hash):
        raise invalid
    if not account.enabled:
        raise invalid
    if account.expires_at is not None and account.expires_at < datetime.now(UTC):
        raise invalid
    return _mint_technical_account_tokens(account)


@app.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    x_dms_maintenance_active: str = Header(default="false"),
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    """Emergency shutdown (4.8, P6-S6): the gateway broadcasts the
    maintenance mode status via header on every request it passes through,
    instead of every backend service having to poll `permission-service`
    itself (see ADR 0024). If maintenance mode is active, new logins are
    rejected except for the superuser - the superuser login only works
    independently of that anyway if the account was previously activated
    via break-glass (4.6).

    Auth decoupling from Keycloak (Phase 18, ADR 0063): if the username
    matches a `TechnicalAccount` (currently only the superuser, domain
    admins follow in P18-S3), this endpoint authenticates locally instead
    of forwarding to Keycloak - Keycloak's reachability therefore no longer
    matters for the superuser login."""
    maintenance_active = x_dms_maintenance_active.lower() == "true"
    if maintenance_active and payload.username != superuser.SUPERUSER_USERNAME:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Systemweite Notfallsperre aktiv - Login nur für den Superuser möglich",
        )

    technical_account = await session.scalar(
        select(TechnicalAccount).where(TechnicalAccount.username == payload.username)
    )
    if technical_account is not None:
        return await _login_technical_account(technical_account, payload.password)

    try:
        tokens = await keycloak_client.login(settings, payload.username, payload.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return TokenResponse(**tokens)


async def _refresh_technical_account_token(
    refresh_token_value: str, session: AsyncSession
) -> TokenResponse:
    """Auth decoupling from Keycloak (Phase 18, ADR 0063) - counterpart to
    `keycloak_client.refresh` for locally issued refresh tokens: no real
    Keycloak refresh grant possible (purely local accounts have no Keycloak
    session), instead a renewed signature check via the already-existing
    `combined_validator` and a fresh token pair, provided the underlying
    account is still active."""
    try:
        claims = app.state.combined_validator.validate(refresh_token_value)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungültiges Refresh-Token"
        ) from exc
    account = await session.get(TechnicalAccount, int(claims["sub"]))
    if account is None or not account.enabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Konto nicht aktiv")
    if account.expires_at is not None and account.expires_at < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Aktivierung abgelaufen"
        )
    return _mint_technical_account_tokens(account)


@app.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    payload: RefreshRequest, session: AsyncSession = Depends(get_session)
) -> TokenResponse:
    if local_token_issuer.is_local_token(payload.refresh_token):
        return await _refresh_technical_account_token(payload.refresh_token, session)
    try:
        tokens = await keycloak_client.refresh(settings, payload.refresh_token)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return TokenResponse(**tokens)


@app.get("/me")
async def me(
    user: dict = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> dict:
    """Normalized identity from the token (4.4) - the translation into
    internal DMS roles is handled by the Permission Service (4.1, P2-S2),
    not the Auth Service itself.

    Since P24-S2: `realm_roles` contains, in addition to Keycloak's raw
    `realm_access.roles`, also the roles derived from the `groups` JWT
    claim (`ad_group_mapping.resolve_roles_for_groups`, configurable AD
    group->role mapping, 4.4) - deliberately merged into the same list
    instead of a separate field, since every existing caller
    (`permission-service`'s role assignment reconciliation, frontend role
    checks) already reads `realm_roles`, and a role should be treated the
    same from the rest of the system's perspective regardless of whether it
    was assigned directly as a Keycloak realm role or derived via a group
    membership - duplicates (e.g. the same role both assigned directly and
    derived via a group) are deduplicated."""
    realm_roles = list(user.get("realm_access", {}).get("roles", []))
    mapped_roles = await ad_group_mapping.resolve_roles_for_groups(
        session, user.get("groups", []) or []
    )
    return {
        "sub": user.get("sub"),
        "username": user.get("preferred_username"),
        "email": user.get("email"),
        "realm_roles": list(dict.fromkeys(realm_roles + mapped_roles)),
    }


@app.get("/me/preferences", response_model=ThemePreference)
def get_my_preferences(user: dict = Depends(get_current_user)) -> ThemePreference:
    """Cross-UI theming (8, P4-S6) - the preference is attached to the user
    account (Keycloak attribute), not a single installation/single browser,
    so it applies across devices (user feedback after P4-S5)."""
    theme = admin_users.get_theme_preference(app.state.keycloak_admin, user["sub"])
    return ThemePreference(theme=theme)


@app.put("/me/preferences", response_model=ThemePreference)
def update_my_preferences(
    payload: ThemePreference, user: dict = Depends(get_current_user)
) -> ThemePreference:
    admin_users.set_theme_preference(app.state.keycloak_admin, user["sub"], payload.theme)
    return payload


async def _require_permission(user: dict, permission: str, message: str) -> None:
    """Generic capability check against permission-service (post-roadmap
    Phase 19 Session 3, ADR 0068) - `_require_user_management` below
    remains as a named special case with its own error message, new callers
    (`lookup_user`/`search_directory`) use this helper directly."""
    allowed = await app.state.permission_client.has_permission(user["sub"], permission)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


async def _require_user_management(user: dict) -> None:
    """Retrofit (P6-S5, 4.6): user management has been ungated since P4-S3 -
    "user/permission management" domain, now enforced via a real
    permission-service check instead of an `X-DMS-Roles` string comparison
    (the role natively lives in permission-service, not Keycloak)."""
    await _require_permission(
        user, "admin.user_management", "Fehlende Domain-Admin-Rolle 'Nutzer-/Rechteverwaltung'"
    )


@app.get("/users", response_model=list[UserOut])
async def list_users(user: dict = Depends(get_current_user)) -> list[dict]:
    """User management for the admin UI (8, since P4-S3) - reads directly
    from Keycloak, no own user table (see README: accounts are already
    fully covered by Keycloak)."""
    await _require_user_management(user)
    return admin_users.list_users(app.state.keycloak_admin)


@app.get("/users/lookup", response_model=UserLookupOut)
async def lookup_user(username: str, user: dict = Depends(get_current_user)) -> dict:
    """Exact name resolution (2.5, P14-S6) - deliberately WITHOUT the
    `admin.user_management` gate like `GET /users` above, see
    `admin_users.find_user_by_username`. Since post-roadmap Phase 19
    Session 3 (ADR 0068), gated via the "everyone" group from
    permission-service (`users.lookup`, pre-seeded since P19-S2) instead of
    being completely open - the actual behavior doesn't change (every
    authenticated principal is implicitly a member), but the permission is
    now natively admin-editable instead of hardcoded."""
    await _require_permission(
        user, "users.lookup", "Fehlende Berechtigung 'users.lookup' (everyone-Gruppe entzogen?)"
    )
    match = admin_users.find_user_by_username(app.state.keycloak_admin, username)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Nutzer {username!r} unbekannt")
    return match


@app.get("/users/directory", response_model=list[DirectoryEntryOut])
async def search_directory(q: str, user: dict = Depends(get_current_user)) -> list[dict]:
    """Directory for finding other employees (2.5/4.4, P15-S4) - no
    `admin.user_management` gate, see `admin_users.search_users`. Since
    post-roadmap Phase 19 Session 3 (ADR 0068), gated via the "everyone"
    group from permission-service (`users.directory`, pre-seeded since
    P19-S2) instead of being completely open - same principle as
    `lookup_user` above."""
    await _require_permission(
        user,
        "users.directory",
        "Fehlende Berechtigung 'users.directory' (everyone-Gruppe entzogen?)",
    )
    return admin_users.search_users(app.state.keycloak_admin, q)


@app.get("/users/directory/federation-status", response_model=DirectoryFederationStatusOut)
async def directory_federation_status(
    session: AsyncSession = Depends(get_session),
) -> DirectoryFederationStatusOut:
    """Whether the federated contact directory search is enabled on this
    installation (2.5: "own, explicitly opt-in configurable capability") -
    the frontend only shows the corresponding UI section when
    `enabled=true`."""
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
    """Cross-installation contact directory search (2.5/7.4, P15-S4) -
    queries every peer installation known via the Federation Hub and
    approved for the contact directory search DIRECTLY (not relayed
    through the Hub, see ADR 0054). Returns exclusively hits from OTHER
    installations - the frontend additionally calls `GET /users/directory`
    for local ones."""
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
    """Receives a signed directory search request from a peer installation
    (2.5/7.4, P15-S4) - deliberately public (no `X-DMS-Principal`,
    analogous to `workflow_service.main.federation_inbound`), instead
    authenticates via `X-Installation-Signature`, verified against the
    REQUESTING installation's public key stored at the Hub (fetched live,
    no locally cached peer key store - see ADR 0054)."""
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
    """Installation-wide user count (9.1 "named accounts" model, since
    P9-S1) - ungated for `license-service`'s internal call (no service has
    a real Keycloak bearer token for `Depends(get_current_user)`, see the
    PROGRESS.md research)."""
    return {"count": len(admin_users.list_users(app.state.keycloak_admin))}


@app.get("/sessions/count")
def count_sessions() -> dict:
    """Installation-wide count of concurrent sessions (9.1 "concurrent
    users" model, since P9-S1) - ready-made Keycloak admin API method, no
    new session tracking needed. Ungated, same rationale as
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


@app.get("/users/{user_id}", response_model=UserLookupOut)
async def get_user(user_id: str, user: dict = Depends(get_current_user)) -> dict:
    """Reverse identity resolution (post-roadmap Phase 19 Session 4, ADR
    0069) - counterpart to `GET /users/lookup` above (name -> UUID):
    `teamspace-service`'s member lists and `permission-service`'s
    delegations only know the raw `principal_id` UUID, no frontend could
    previously resolve it into a username. Same gate as `lookup_user`
    (`users.lookup`, "everyone" group) - same trust level, just the search
    direction is reversed. **Must be registered after all static
    `/users/...` routes** (`/users/lookup`, `/users/directory`,
    `/users/count`, ...) - FastAPI matches paths in registration order, an
    earlier-registered `/users/{user_id}` would otherwise shadow them."""
    await _require_permission(
        user, "users.lookup", "Fehlende Berechtigung 'users.lookup' (everyone-Gruppe entzogen?)"
    )
    match = admin_users.find_user_by_id(app.state.keycloak_admin, user_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Nutzer {user_id!r} unbekannt")
    return match


@app.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, user: dict = Depends(get_current_user)) -> None:
    await _require_user_management(user)
    try:
        admin_users.delete_user(app.state.keycloak_admin, user_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


# Keycloak creates these realm roles itself (default behavior since
# forever) - a package-driven list (14.1/P17-S1) should not list them as an
# "own" DMS role.
_KEYCLOAK_BUILTIN_REALM_ROLE_PREFIXES = ("default-roles-",)
_KEYCLOAK_BUILTIN_REALM_ROLE_NAMES = {"offline_access", "uma_authorization"}


def _is_builtin_realm_role(name: str) -> bool:
    return name in _KEYCLOAK_BUILTIN_REALM_ROLE_NAMES or name.startswith(
        _KEYCLOAK_BUILTIN_REALM_ROLE_PREFIXES
    )


async def _require_service_user_management(x_dms_principal: str) -> None:
    """Like `_require_user_management` above, but for service-to-service
    calls without a Keycloak JWT (`X-DMS-Principal` header instead of
    `Depends(get_current_user)`) - identical pattern to
    `workflow_service.main._require_object_config`. Used by
    `config-service`'s configuration package import (14.1, P17-S1), which
    needs to be able to create new realm roles under its own name
    (`X-DMS-Principal: config-service`)."""
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
    """Currently existing Keycloak realm roles (14.1, P17-S1) - basis for
    the export branch of a configuration package (`config-service`'s
    `realm_roles` category). Ungated like `GET /users/count` etc. - returns
    only names, no sensitive data, identical trust model to
    `permission-service`'s `GET /roles`, which is likewise ungated."""
    roles = app.state.keycloak_admin.get_realm_roles()
    return [{"name": role["name"]} for role in roles if not _is_builtin_realm_role(role["name"])]


@app.post("/realm-roles", status_code=status.HTTP_204_NO_CONTENT)
async def ensure_realm_roles(
    payload: RealmRolesRequest, x_dms_principal: str = Header(default="")
) -> None:
    """Creates the given realm roles idempotently (14.1, P17-S1) - identical
    primitive to `bootstrap._ensure_dms_admin_role`
    (`create_realm_role(..., skip_exists=True)`), here for arbitrary names
    supplied by a configuration package (e.g. `dms-poststelle`, 2.5)
    instead of hardcoded for `dms-admin`. Only creates the role, does not
    assign it to anyone - assignment continues to happen outside this
    service via the Keycloak Admin Console (see `bootstrap.py`)."""
    await _require_service_user_management(x_dms_principal)
    for name in payload.names:
        app.state.keycloak_admin.create_realm_role(payload={"name": name}, skip_exists=True)


@app.get("/ad-group-mappings", response_model=list[AdGroupRoleMappingOut])
async def list_ad_group_mappings(
    user: dict = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> list[AdGroupRoleMapping]:
    """AD group->internal role mapping (4.4, P24-S2, admin CRUD) - gated
    like `GET /users` (`admin.user_management`, same "user/permission
    management" domain, since a misconfigured mapping can silently grant
    users additional roles)."""
    await _require_user_management(user)
    return await ad_group_mapping.list_mappings(session)


@app.post(
    "/ad-group-mappings",
    response_model=AdGroupRoleMappingOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_ad_group_mapping(
    payload: AdGroupRoleMappingIn,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AdGroupRoleMapping:
    """Creates a new mapping - takes effect starting with the next `GET
    /me` resolution (no caching delay, see
    `ad_group_mapping.resolve_roles_for_groups`). Audited via
    `auth.ad_group_role_mapping.created` (`audit-service` already consumes
    `auth.>`, no new audit mechanism needed) as well as `created_by`/
    `created_at` directly on the row."""
    await _require_user_management(user)
    mapping = await ad_group_mapping.create_mapping(
        session,
        ad_group_name=payload.ad_group_name,
        role_name=payload.role_name,
        created_by=user.get("preferred_username") or user.get("sub"),
    )
    await session.commit()
    await publish_event(
        "auth.ad_group_role_mapping.created",
        {
            "id": mapping.id,
            "ad_group_name": mapping.ad_group_name,
            "role_name": mapping.role_name,
        },
        actor=user.get("sub"),
    )
    return mapping


@app.delete("/ad-group-mappings/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ad_group_mapping(
    mapping_id: int,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Deletes a mapping - takes effect the same way as creation, starting
    with the next `GET /me` resolution. `404` for an unknown `mapping_id`."""
    await _require_user_management(user)
    try:
        mapping = await ad_group_mapping.delete_mapping(session, mapping_id)
    except ad_group_mapping.MappingNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "auth.ad_group_role_mapping.deleted",
        {
            "id": mapping_id,
            "ad_group_name": mapping.ad_group_name,
            "role_name": mapping.role_name,
        },
        actor=user.get("sub"),
    )


@app.get("/superuser/status", response_model=SuperuserStatus)
async def get_superuser_status() -> SuperuserStatus:
    """Break-glass status (4.6) for the admin UI banner display - activation
    itself runs through the Permission Service's already-existing generic
    approval flow (`POST /approval-requests` with
    `action_type="auth.superuser.activate"`), not through an endpoint
    here."""
    try:
        active, expires_at = await superuser.get_status(app.state.session_factory)
    except superuser.SuperuserNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SuperuserStatus(
        active=active,
        expires_at=expires_at.isoformat() if expires_at else None,
        principal_id=await superuser.get_principal_id(app.state.session_factory),
    )


@app.post("/superuser/deactivate", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_superuser() -> None:
    """Early, voluntary termination of the activation (4.6) - complements
    the automatic expiry via the poll loop."""
    try:
        await superuser.deactivate(app.state.session_factory)
    except superuser.SuperuserNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    # No authenticated caller context at this endpoint - the deactivated
    # superuser account itself is the next-best attribution (there's only
    # one, see get_superuser_status).
    await publish_event(
        "auth.superuser.deactivated",
        {"reason": "manual"},
        actor=await superuser.get_principal_id(app.state.session_factory),
    )


# --- SSO/automatic login (post-roadmap feature, Kerberos/SPNEGO via
# Keycloak) --------------------------------------------------------------


async def _get_or_create_sso_config(session: AsyncSession) -> SsoConfig:
    config = await session.get(SsoConfig, _SSO_CONFIG_ID)
    if config is None:
        config = SsoConfig(id=_SSO_CONFIG_ID, enabled=False, updated_at=datetime.now(UTC))
        session.add(config)
        await session.flush()
    return config


def _redirect_uri_origin_allowed(redirect_uri: str) -> bool:
    """Open-redirect protection for `GET /oidc/authorize`/`POST /oidc/
    callback` - `redirect_uri` comes from the client, so it must be checked
    against a fixed allow list, exactly the same principle as
    gateway-service's `cors_allowed_origins`."""
    parsed = urlparse(redirect_uri)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin in settings.sso_redirect_uri_allowed_origins


@app.get("/oidc/authorize", response_model=OidcAuthorizeOut)
async def oidc_authorize(redirect_uri: str, state: str) -> OidcAuthorizeOut:
    """SSO/automatic login - the login entry point itself, therefore public
    (no DMS token exists at this point yet). Returns only the URL, the
    client navigates there itself (no server-side redirect, consistent with
    the rest of this project's service style)."""
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
    """Exchanges the `code` delivered by Keycloak's redirect for tokens
    server-side - identical response format to `POST /login`, so nothing
    needs to change in the frontend's token storage mechanism. Public like
    `/oidc/authorize` (the caller has no DMS token yet).

    Same emergency-shutdown lock as `POST /login` (4.8) - there it's
    checked BEFORE the actual login, here only AFTERWARD (the username is
    not known before the code exchange): if maintenance mode is active and
    the token does not belong to the superuser, the freshly issued tokens
    are discarded instead of being returned - otherwise SSO would bypass
    the lock that the form login already enforces."""
    if not _redirect_uri_origin_allowed(payload.redirect_uri):
        raise HTTPException(status_code=400, detail="redirect_uri nicht erlaubt")
    try:
        tokens = await keycloak_client.exchange_code(
            settings, code=payload.code, redirect_uri=payload.redirect_uri
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    if x_dms_maintenance_active.lower() == "true":
        claims = _keycloak_validator.validate(tokens["access_token"])
        if claims.get("preferred_username") != superuser.SUPERUSER_USERNAME:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Systemweite Notfallsperre aktiv - Login nur für den Superuser möglich",
            )
    return TokenResponse(**tokens)


@app.get("/sso-config", response_model=SsoConfigOut)
async def get_sso_config(session: AsyncSession = Depends(get_session)) -> SsoConfigOut:
    """Ungated like `GET /share-link-config` in document-service - purely
    information about whether SSO is active, no sensitive data.
    `login/page.tsx` queries this BEFORE showing the password form."""
    config = await _get_or_create_sso_config(session)
    await session.commit()
    return config


@app.put("/sso-config", response_model=SsoConfigOut)
async def put_sso_config(
    payload: SsoConfigIn,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SsoConfigOut:
    """Unlike `GET`, gated - turning SSO on/off is security-relevant
    (`admin.user_management`, same domain as user management itself)."""
    await _require_user_management(user)
    config = await _get_or_create_sso_config(session)
    config.enabled = payload.enabled
    config.updated_at = datetime.now(UTC)
    await session.commit()
    return config


@app.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: LogoutRequest) -> None:
    """Actually ends the session on Keycloak's side (see
    `keycloak_client.end_session` docstring) - without this endpoint there
    was previously NO logout mechanism at all, "log out" only deleted
    local tokens. Best-effort from the frontend's perspective
    (`auth-context.tsx`'s `logout()` does not block the local logout on an
    error here)."""
    await keycloak_client.end_session(settings, payload.refresh_token)
