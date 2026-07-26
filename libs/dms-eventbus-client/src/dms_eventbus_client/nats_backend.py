import nats
from nats.js import api
from nats.js.client import JetStreamContext
from nats.js.errors import NotFoundError

from dms_eventbus_client.interface import EventBusClient, MessageHandler


class NatsEventBusClient(EventBusClient):
    """Werkseinstellung aus Konzept 3.4: NATS JetStream als Event-Bus-Backend.

    ``ensure_stream=True`` (Default) ist für **Producer** gedacht: ``connect()``
    legt den eigenen Stream an, falls er noch nicht existiert. Reine
    **Konsumenten** wie der Audit Service, die Ereignisse mehrerer fremder
    Streams abonnieren, verwenden ``ensure_stream=False`` und geben keinen
    ``stream``-Namen an - JetStream löst den passenden Stream beim Abonnieren
    automatisch anhand des Subjects auf (siehe ADR 0001).
    """

    def __init__(self, url: str, stream: str | None = None, *, ensure_stream: bool = True) -> None:
        if ensure_stream and stream is None:
            raise ValueError("stream ist erforderlich, wenn ensure_stream=True")
        self._url = url
        self._stream = stream
        self._ensure_stream = ensure_stream
        self._nc: nats.NATS | None = None
        self._js: JetStreamContext | None = None

    async def connect(self) -> None:
        self._nc = await nats.connect(self._url)
        self._js = self._nc.jetstream()
        if self._ensure_stream:
            assert self._stream is not None
            try:
                await self._js.stream_info(self._stream)
            except NotFoundError:
                await self._js.add_stream(name=self._stream, subjects=[f"{self._stream}.>"])

    async def publish(self, subject: str, payload: bytes) -> None:
        assert self._js is not None, "connect() muss vor publish() aufgerufen werden"
        await self._js.publish(subject, payload)

    async def subscribe(
        self, subject: str, handler: MessageHandler, *, durable: str, deliver_new: bool = False
    ) -> None:
        assert self._js is not None, "connect() muss vor subscribe() aufgerufen werden"

        async def _callback(msg) -> None:
            await handler(msg.data)
            await msg.ack()

        deliver_policy = api.DeliverPolicy.NEW if deliver_new else None
        await self._js.subscribe(
            subject, durable=durable, cb=_callback, deliver_policy=deliver_policy
        )

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.close()
