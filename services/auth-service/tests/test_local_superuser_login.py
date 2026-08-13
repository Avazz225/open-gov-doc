"""Auth-Entkopplung von Keycloak (Post-Roadmap-Feature, Phase 18 Session 2,
ADR 0063) - der Superuser lebt seit dieser Session als `TechnicalAccount`-
Zeile, `POST /login`/`POST /refresh` authentifizieren ihn lokal statt gegen
Keycloak. Diese Tests decken den kompletten Login-Kreislauf ab, unabhängig
von `test_superuser.py` (das nur die Modulfunktionen direkt testet)."""

from datetime import UTC, datetime, timedelta

from auth_service import superuser
from auth_service.models import TechnicalAccount
from sqlalchemy import select


def test_superuser_login_fails_while_not_activated(client):
    response = client.post("/login", json={"username": "superuser", "password": "superuser"})

    assert response.status_code == 401


async def test_superuser_login_succeeds_after_activation(client, session_factory):
    await superuser.activate(session_factory, activation_minutes=30)

    response = client.post("/login", json={"username": "superuser", "password": "superuser"})

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"


async def test_superuser_login_wrong_password_fails_even_when_activated(client, session_factory):
    await superuser.activate(session_factory, activation_minutes=30)

    response = client.post("/login", json={"username": "superuser", "password": "wrong"})

    assert response.status_code == 401


async def test_locally_issued_access_token_works_on_me_endpoint(client, session_factory):
    await superuser.activate(session_factory, activation_minutes=30)
    tokens = client.post("/login", json={"username": "superuser", "password": "superuser"}).json()

    response = client.get("/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})

    assert response.status_code == 200
    assert response.json()["username"] == "superuser"


async def test_locally_issued_refresh_token_issues_a_fresh_pair(client, session_factory):
    await superuser.activate(session_factory, activation_minutes=30)
    tokens = client.post("/login", json={"username": "superuser", "password": "superuser"}).json()

    response = client.post("/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 200
    refreshed = response.json()
    assert refreshed["access_token"] != tokens["access_token"]


async def test_refresh_fails_after_deactivation(client, session_factory):
    await superuser.activate(session_factory, activation_minutes=30)
    tokens = client.post("/login", json={"username": "superuser", "password": "superuser"}).json()

    await superuser.deactivate(session_factory)

    response = client.post("/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 401


async def test_refresh_fails_after_expiry(client, session_factory):
    """Aktiviert normal und loggt sich WÄHREND das Konto noch gültig ist ein
    (`activation_minutes=-1` direkt an `activate()` würde bereits den Login
    selbst mit 401 ablehnen, siehe `_login_technical_account`s eigene
    `expires_at`-Prüfung - das würde den Refresh-Pfad nie erreichen). Setzt
    `expires_at` erst NACH dem erfolgreichen Login manuell in die
    Vergangenheit, um gezielt die eigenständige `expires_at`-Prüfung im
    Refresh-Pfad zu testen, unabhängig vom Login-Pfad."""
    await superuser.activate(session_factory, activation_minutes=30)
    tokens = client.post("/login", json={"username": "superuser", "password": "superuser"}).json()

    async with session_factory() as session:
        account = await session.scalar(
            select(TechnicalAccount).where(
                TechnicalAccount.username == superuser.SUPERUSER_USERNAME
            )
        )
        account.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    response = client.post("/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 401
