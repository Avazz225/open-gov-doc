import asyncio
import logging
from collections.abc import Awaitable, Callable

import nats
from nats.js import api
from nats.js.client import JetStreamContext
from nats.js.errors import NotFoundError

from dms_eventbus_client.interface import EventBusClient, MessageHandler, SubjectNotFoundError

logger = logging.getLogger(__name__)


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
        self,
        subject: str,
        handler: MessageHandler,
        *,
        durable: str,
        deliver_new: bool = False,
        max_concurrency: int | Callable[[], Awaitable[int]] = 1,
    ) -> None:
        assert self._js is not None, "connect() muss vor subscribe() aufgerufen werden"

        in_flight = 0
        slot_free = asyncio.Condition()

        async def _resolve_limit() -> int:
            if callable(max_concurrency):
                return await max_concurrency()
            return max_concurrency

        async def _run(msg) -> None:
            nonlocal in_flight
            try:
                await handler(msg.data)
            except Exception:
                # Wie im sequentiellen Pfad: bei einem Fehler wird nicht
                # bestätigt (Neuzustellung durch JetStream) - hier zusätzlich
                # geloggt, da die Exception sonst nur als "Task exception was
                # never retrieved" verlorenginge (Task statt direktem await).
                logger.exception(
                    "Handler-Fehler bei nebenläufiger Verarbeitung (Subject %r) - "
                    "keine Bestätigung, Neuzustellung erwartet",
                    subject,
                )
            else:
                await msg.ack()
            finally:
                async with slot_free:
                    in_flight -= 1
                    slot_free.notify_all()

        async def _callback(msg) -> None:
            nonlocal in_flight
            limit = await _resolve_limit()
            if limit <= 1:
                # Exakt das bisherige Verhalten (keine Nebenläufigkeit) -
                # unverändert für jeden Aufrufer, der max_concurrency nicht setzt.
                await handler(msg.data)
                await msg.ack()
                return
            async with slot_free:
                while in_flight >= limit:
                    await slot_free.wait()
                in_flight += 1
            asyncio.create_task(_run(msg))

        deliver_policy = api.DeliverPolicy.NEW if deliver_new else None
        try:
            await self._js.subscribe(
                subject, durable=durable, cb=_callback, deliver_policy=deliver_policy
            )
        except NotFoundError as exc:
            raise SubjectNotFoundError(
                f"Kein Stream deckt Subject {subject!r} ab - noch kein Producer gestartet?"
            ) from exc

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.close()
