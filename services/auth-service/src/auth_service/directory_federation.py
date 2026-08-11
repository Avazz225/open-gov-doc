import json
import logging

import httpx

from auth_service import federation_crypto
from auth_service.models import FederationIdentity

logger = logging.getLogger(__name__)

# Kapazitäts-Marker (2.5/7.4, P15-S4): `Installation.supported_process_types`
# ist ein generisches, bereits bestehendes String-Listenfeld im Adressbuch
# des Federation Hub - hier zweckentfremdet als Fähigkeits-Kennung für "diese
# Registrierung ist eine Kontaktsuche-Teilnahme", nicht ein echter
# BPMN-Prozesstyp. Bewusst KEIN neues Feld in federation-hub-service (kein
# Code-Change an einem bereits stabilen, von anderen Services genutzten
# Service nötig) - siehe ADR 0054.
CONTACT_DIRECTORY_CAPABILITY = "dms.contact-directory.v1"


class PeerQueryError(Exception):
    pass


async def query_peer(installation: dict, identity: FederationIdentity, query: str) -> list[dict]:
    """Direkter, signierter Aufruf gegen die andere Installation - NICHT über
    den Hub relayt (der vermittelt hier nur die Adress-/Schlüssel-Auffindung
    über sein bereits ungegatet lesbares Adressbuch, siehe ADR 0054)."""
    body = json.dumps({"query": query}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Installation-Id": identity.installation_id,
        "X-Installation-Signature": federation_crypto.sign_body(identity.private_key_pem, body),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{installation['callback_base_url']}/users/directory/federated-search-inbound",
                content=body,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        raise PeerQueryError(
            f"Installation {installation['id']!r} nicht erreichbar oder Anfrage abgelehnt: {exc}"
        ) from exc


def eligible_peers(installations: list[dict], own_installation_id: str) -> list[dict]:
    return [
        inst
        for inst in installations
        if inst["id"] != own_installation_id
        and inst.get("revoked_at") is None
        and CONTACT_DIRECTORY_CAPABILITY in inst.get("supported_process_types", [])
    ]


async def search_all_peers(
    installations: list[dict], identity: FederationIdentity, query: str
) -> list[dict]:
    """Fragt jede berechtigte Peer-Installation einzeln ab (2.5) - ein
    einzelner nicht erreichbarer/ablehnender Peer blockiert die übrigen
    nicht (`PeerQueryError` wird geloggt und übersprungen, kein
    Gesamtabbruch)."""
    results: list[dict] = []
    for installation in eligible_peers(installations, identity.installation_id):
        try:
            entries = await query_peer(installation, identity, query)
        except PeerQueryError:
            logger.warning(
                "federated_directory_peer_query_failed",
                extra={"installation_id": installation["id"]},
            )
            continue
        for entry in entries:
            entry["installation_id"] = installation["id"]
            entry["installation_display_name"] = installation["display_name"]
        results.extend(entries)
    return results
