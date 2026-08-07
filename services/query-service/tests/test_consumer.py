from dms_eventbus_client import Event
from query_service.consumer import make_handler


class FakeDocumentClient:
    def __init__(self, documents: dict[str, dict]):
        self._documents = documents

    async def get_document(self, document_id: str) -> dict | None:
        return self._documents.get(document_id)

    async def update_document(self, document_id: str, *, attributes: dict) -> dict:
        self._documents[document_id] = {**self._documents[document_id], "attributes": attributes}
        return self._documents[document_id]


class FakeClients:
    def __init__(self, document_client=None, object_type_client=None, permission_client=None):
        self.document_client = document_client
        self.object_type_client = object_type_client
        self.permission_client = permission_client


def _approved_event(action_type: str, params: dict, principal_id: str = "alice") -> bytes:
    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        subject=None,
        payload={
            "action_type": action_type,
            "payload": {"params": params, "principal_id": principal_id},
        },
        actor="bob",
    )
    return event.to_bytes()


async def test_handler_executes_known_action_on_approval():
    documents = {"doc-1": {"id": "doc-1", "attributes": {"notiz": "alt"}}}
    clients = FakeClients(document_client=FakeDocumentClient(documents))
    published: list[tuple] = []

    async def publish_event(event_type, subject, payload, actor):
        published.append((event_type, subject, payload, actor))

    handler = make_handler(clients, publish_event)
    await handler(
        _approved_event(
            "document.attribute_reset", {"document_id": "doc-1", "attribute_key": "notiz"}
        )
    )

    assert documents["doc-1"]["attributes"] == {}
    assert published[0][0] == "query.manipulation.executed"
    assert published[0][3] == "alice"


async def test_handler_ignores_unknown_action_type():
    clients = FakeClients()
    published = []

    async def publish_event(event_type, subject, payload, actor):
        published.append(event_type)

    handler = make_handler(clients, publish_event)
    await handler(_approved_event("document.force_unlock", {"document_id": "doc-1"}))

    assert published == []


async def test_handler_publishes_failure_event_on_execution_error():
    clients = FakeClients(document_client=FakeDocumentClient({}))
    published = []

    async def publish_event(event_type, subject, payload, actor):
        published.append(event_type)

    handler = make_handler(clients, publish_event)
    await handler(
        _approved_event(
            "document.attribute_reset", {"document_id": "does-not-exist", "attribute_key": "x"}
        )
    )

    assert published == ["query.manipulation.execution_failed"]


async def test_handler_ignores_missing_params_defensively():
    clients = FakeClients()
    published = []

    async def publish_event(event_type, subject, payload, actor):
        published.append(event_type)

    event = Event(
        event_type="permission.approval.approved",
        service_name="permission-service",
        subject=None,
        payload={"action_type": "document.attribute_reset", "payload": {}},
        actor="bob",
    )

    handler = make_handler(clients, publish_event)
    await handler(event.to_bytes())

    assert published == []
