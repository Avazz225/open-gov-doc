from dataclasses import dataclass, field

import pytest
from query_service.manipulation import (
    ACTIONS,
    UnknownActionError,
    get_action,
)


class FakeDocumentClient:
    def __init__(self, documents: dict[str, dict]):
        self._documents = documents
        self.updated: list[tuple[str, dict]] = []

    async def get_document(self, document_id: str) -> dict | None:
        return self._documents.get(document_id)

    async def update_document(self, document_id: str, *, attributes: dict) -> dict:
        self.updated.append((document_id, attributes))
        self._documents[document_id] = {**self._documents[document_id], "attributes": attributes}
        return self._documents[document_id]


class FakeObjectTypeClient:
    def __init__(self, object_types: dict[int, dict]):
        self._object_types = object_types
        self.updated: list[tuple[int, dict]] = []

    async def get_object_type(self, object_type_id: int) -> dict | None:
        return self._object_types.get(object_type_id)

    async def update_object_type(self, object_type_id: int, payload: dict) -> dict:
        self.updated.append((object_type_id, payload))
        self._object_types[object_type_id] = {**self._object_types[object_type_id], **payload}
        return self._object_types[object_type_id]


class FakePermissionClient:
    def __init__(self, assignments: dict[int, dict], roles: dict[int, dict]):
        self._assignments = assignments
        self._roles = roles
        self.deleted: list[int] = []

    async def get_role_assignment(self, role_assignment_id: int) -> dict | None:
        return self._assignments.get(role_assignment_id)

    async def get_role(self, role_id: int) -> dict | None:
        return self._roles.get(role_id)

    async def delete_role_assignment(self, role_assignment_id: int) -> None:
        self.deleted.append(role_assignment_id)


@dataclass
class FakeClients:
    document_client: FakeDocumentClient = field(default=None)
    object_type_client: FakeObjectTypeClient = field(default=None)
    permission_client: FakePermissionClient = field(default=None)


def test_action_registry_contains_expected_actions():
    assert set(ACTIONS) == {
        "document.attribute_reset",
        "permission.role_assignment.delete",
        "object_type.update",
    }
    assert ACTIONS["document.attribute_reset"].is_critical is False
    assert ACTIONS["permission.role_assignment.delete"].is_critical is True
    assert ACTIONS["object_type.update"].is_critical is True


def test_get_action_raises_for_unknown_type():
    with pytest.raises(UnknownActionError):
        get_action("does.not.exist")


async def test_document_attribute_reset_dry_run_and_execute():
    documents = {"doc-1": {"id": "doc-1", "attributes": {"notiz": "alt", "andere": "bleibt"}}}
    clients = FakeClients(document_client=FakeDocumentClient(documents))
    action = get_action("document.attribute_reset")
    params = {"document_id": "doc-1", "attribute_key": "notiz"}

    preview = await action.dry_run(params, clients)
    assert "notiz" in preview
    assert "alt" in preview

    result = await action.execute(params, clients)
    assert result["attributes"] == {"andere": "bleibt"}
    assert clients.document_client.updated == [("doc-1", {"andere": "bleibt"})]


async def test_document_attribute_reset_dry_run_missing_document_raises():
    clients = FakeClients(document_client=FakeDocumentClient({}))
    action = get_action("document.attribute_reset")
    with pytest.raises(ValueError):
        await action.dry_run({"document_id": "doc-x", "attribute_key": "k"}, clients)


async def test_role_assignment_delete_dry_run_and_execute():
    assignments = {
        393: {
            "id": 393,
            "principal_type": "user",
            "principal_id": "alice",
            "role_id": 5,
            "resource_id": "root",
        }
    }
    roles = {5: {"id": 5, "name": "reader-role", "permissions": ["document.read"]}}
    clients = FakeClients(permission_client=FakePermissionClient(assignments, roles))
    action = get_action("permission.role_assignment.delete")
    params = {"role_assignment_id": 393}

    preview = await action.dry_run(params, clients)
    assert "alice" in preview
    assert "reader-role" in preview

    result = await action.execute(params, clients)
    assert result == {"role_assignment_id": 393, "deleted": True}
    assert clients.permission_client.deleted == [393]


async def test_role_assignment_delete_dry_run_missing_assignment_raises():
    clients = FakeClients(permission_client=FakePermissionClient({}, {}))
    action = get_action("permission.role_assignment.delete")
    with pytest.raises(ValueError):
        await action.dry_run({"role_assignment_id": 999}, clients)


async def test_object_type_update_dry_run_and_execute():
    object_types = {
        42: {
            "id": 42,
            "name": "Rechnung",
            "attributes": [],
            "naming_constraints": None,
            "conditions": [],
            "allowed_parent_types": None,
            "icon": None,
            "kennzeichen_format": None,
            "kennzeichen_display_override": None,
            "required_signature_level": None,
            "default_retention_days": None,
            "deletion_reason_required_override": None,
            "default_archive_after_days": None,
            "archive_encryption_enabled": False,
        }
    }
    clients = FakeClients(object_type_client=FakeObjectTypeClient(object_types))
    action = get_action("object_type.update")
    params = {"object_type_id": 42, "field": "conditions", "value": [{"op": "required"}]}

    preview = await action.dry_run(params, clients)
    assert "Rechnung" in preview
    assert "conditions" in preview

    result = await action.execute(params, clients)
    assert result == {"object_type_id": 42, "conditions": [{"op": "required"}]}


async def test_object_type_update_rejects_non_whitelisted_field():
    object_types = {42: {"id": 42, "name": "Rechnung", "archive_encryption_enabled": False}}
    clients = FakeClients(object_type_client=FakeObjectTypeClient(object_types))
    action = get_action("object_type.update")
    params = {"object_type_id": 42, "field": "archive_encryption_enabled", "value": True}

    with pytest.raises(ValueError):
        await action.dry_run(params, clients)
    with pytest.raises(ValueError):
        await action.execute(params, clients)
