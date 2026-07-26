import nats
from nats.js.client import JetStreamContext
from nats.js.errors import NotFoundError

from dms_eventbus_client.interface import EventBusClient, MessageHandler


class NatsEventBusClient(EventBusClient):
    """Werkseinstellung aus Konzept 3.4: NATS JetStream als Event-Bus-Backend."""

    def __init__(self, url: str, stream: str) -> None:
        self._url = url
        self._stream = stream
        self._nc: nats.NATS | None = None
        self._js: JetStreamContext | None = None

    async def connect(self) -> None:
        self._nc = await nats.connect(self._url)
        self._js = self._nc.jetstream()
        try:
            await self._js.stream_info(self._stream)
        except NotFoundError:
            await self._js.add_stream(name=self._stream, subjects=[f"{self._stream}.>"])

    async def publish(self, subject: str, payload: bytes) -> None:
        assert self._js is not None, "connect() muss vor publish() aufgerufen werden"
        await self._js.publish(subject, payload)

    async def subscribe(self, subject: str, handler: MessageHandler, *, durable: str) -> None:
        assert self._js is not None, "connect() muss vor subscribe() aufgerufen werden"

        async def _callback(msg) -> None:
            await handler(msg.data)
            await msg.ack()

        await self._js.subscribe(subject, durable=durable, cb=_callback)

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.close()
