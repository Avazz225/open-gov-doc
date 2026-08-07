from dataclasses import dataclass
from typing import Any, Protocol

# Nur diese beiden ObjectType-Felder sind ueber `object_type.update` aenderbar
# - "Objekttyp-/Constraint-Definitionen" (Konzept 6.1s eigene Kategorie fuer
# kritische Tabellen), nicht beliebige ObjectType-Eigenschaften.
_OBJECT_TYPE_UPDATABLE_FIELDS = {"naming_constraints", "conditions"}


class UnknownActionError(Exception):
    pass


class ManipulationClients(Protocol):
    document_client: Any
    object_type_client: Any
    permission_client: Any


@dataclass(frozen=True)
class ManipulationAction:
    action_type: str
    is_critical: bool

    async def dry_run(self, params: dict, clients: ManipulationClients) -> str:
        raise NotImplementedError

    async def execute(self, params: dict, clients: ManipulationClients) -> dict:
        raise NotImplementedError


class DocumentAttributeReset(ManipulationAction):
    """Setzt ein einzelnes benutzerdefiniertes Attribut eines Dokuments
    zurueck - inhaltlich am naechsten an Konzept 6.1s eigenem Beispiel
    ("setze Attribut Y ... zurueck"), aber ehrlich auf ein einzelnes
    Dokument beschraenkt (kein Filter-Bulk, siehe PROGRESS.md fuer die
    Scope-Begruendung). Nicht kritisch - Vier-Augen ist installationsweit
    ueber die bestehende ADR-0022-Konfiguration optional zuschaltbar."""

    def __init__(self) -> None:
        super().__init__(action_type="document.attribute_reset", is_critical=False)

    async def dry_run(self, params: dict, clients: ManipulationClients) -> str:
        document = await clients.document_client.get_document(params["document_id"])
        if document is None:
            raise ValueError(f"Dokument {params['document_id']!r} nicht gefunden")
        current_value = document.get("attributes", {}).get(params["attribute_key"])
        return (
            f"Wuerde Attribut {params['attribute_key']!r} von Dokument "
            f"{params['document_id']!r} von {current_value!r} auf null zuruecksetzen."
        )

    async def execute(self, params: dict, clients: ManipulationClients) -> dict:
        document = await clients.document_client.get_document(params["document_id"])
        if document is None:
            raise ValueError(f"Dokument {params['document_id']!r} nicht gefunden")
        attributes = dict(document.get("attributes", {}))
        attributes.pop(params["attribute_key"], None)
        updated = await clients.document_client.update_document(
            params["document_id"], attributes=attributes
        )
        return {"document_id": params["document_id"], "attributes": updated["attributes"]}


class RoleAssignmentDelete(ManipulationAction):
    """Entfernt eine Rollenzuweisung - deckt Konzept 6.1s Beispielkategorie
    "Berechtigungs-/Rollentabellen" woertlich ab. Kritisch: zwingendes
    Vier-Augen, hartkodiert, nicht konfigurierbar (Konzept-Punkt 4)."""

    def __init__(self) -> None:
        super().__init__(action_type="permission.role_assignment.delete", is_critical=True)

    async def dry_run(self, params: dict, clients: ManipulationClients) -> str:
        assignment = await clients.permission_client.get_role_assignment(
            params["role_assignment_id"]
        )
        if assignment is None:
            raise ValueError(f"Rollenzuweisung {params['role_assignment_id']!r} nicht gefunden")
        role = await clients.permission_client.get_role(assignment["role_id"])
        role_name = role["name"] if role else f"id={assignment['role_id']}"
        return (
            f"Wuerde Rollenzuweisung {params['role_assignment_id']!r} entfernen: "
            f"principal={assignment['principal_id']!r} ({assignment['principal_type']}), "
            f"Rolle={role_name!r}, Ressource={assignment['resource_id']!r}."
        )

    async def execute(self, params: dict, clients: ManipulationClients) -> dict:
        await clients.permission_client.delete_role_assignment(params["role_assignment_id"])
        return {"role_assignment_id": params["role_assignment_id"], "deleted": True}


class ObjectTypeUpdate(ManipulationAction):
    """Aendert ein einzelnes Constraint-Feld (`naming_constraints`/
    `conditions`) eines Objekttyps - deckt Konzept 6.1s Beispielkategorie
    "Objekttyp-/Constraint-Definitionen" ab. Kritisch: zwingendes Vier-Augen,
    hartkodiert."""

    def __init__(self) -> None:
        super().__init__(action_type="object_type.update", is_critical=True)

    def _validate_field(self, field: str) -> None:
        if field not in _OBJECT_TYPE_UPDATABLE_FIELDS:
            raise ValueError(
                f"Feld {field!r} ist ueber diese Aktion nicht aenderbar "
                f"(erlaubt: {sorted(_OBJECT_TYPE_UPDATABLE_FIELDS)})"
            )

    async def dry_run(self, params: dict, clients: ManipulationClients) -> str:
        self._validate_field(params["field"])
        object_type = await clients.object_type_client.get_object_type(params["object_type_id"])
        if object_type is None:
            raise ValueError(f"Objekttyp {params['object_type_id']!r} nicht gefunden")
        current_value = object_type.get(params["field"])
        return (
            f"Wuerde Feld {params['field']!r} von Objekttyp {params['object_type_id']!r} "
            f"({object_type['name']!r}) von {current_value!r} auf {params['value']!r} setzen."
        )

    async def execute(self, params: dict, clients: ManipulationClients) -> dict:
        self._validate_field(params["field"])
        object_type = await clients.object_type_client.get_object_type(params["object_type_id"])
        if object_type is None:
            raise ValueError(f"Objekttyp {params['object_type_id']!r} nicht gefunden")
        payload = {
            key: object_type[key]
            for key in (
                "attributes",
                "naming_constraints",
                "conditions",
                "allowed_parent_types",
                "icon",
                "kennzeichen_format",
                "kennzeichen_display_override",
                "required_signature_level",
                "default_retention_days",
                "deletion_reason_required_override",
                "default_archive_after_days",
                "archive_encryption_enabled",
            )
        }
        payload[params["field"]] = params["value"]
        updated = await clients.object_type_client.update_object_type(
            params["object_type_id"], payload
        )
        field = params["field"]
        return {"object_type_id": params["object_type_id"], field: updated[field]}


ACTIONS: dict[str, ManipulationAction] = {
    action.action_type: action
    for action in (DocumentAttributeReset(), RoleAssignmentDelete(), ObjectTypeUpdate())
}


def get_action(action_type: str) -> ManipulationAction:
    action = ACTIONS.get(action_type)
    if action is None:
        raise UnknownActionError(f"Unbekannter Manipulations-Aktionstyp {action_type!r}")
    return action
