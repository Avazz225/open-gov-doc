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
    """Default from concept 3.4: NATS JetStream as the event bus backend.

    ``ensure_stream=True`` (default) is meant for **producers**: ``connect()``
    creates its own stream if it doesn't already exist. Pure **consumers**
    like the audit service, which subscribe to events from multiple foreign
    streams, use ``ensure_stream=False`` and don't provide a ``stream`` name -
    JetStream automatically resolves the matching stream on subscription
    based on the subject (see ADR 0001).
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
                # As in the sequential path: on failure, the message is not
                # acknowledged (redelivery by JetStream) - additionally
                # logged here, since the exception would otherwise be lost
                # as just a "Task exception was never retrieved" (task
                # instead of a direct await).
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
                # Exactly the previous behavior (no concurrency) - unchanged
                # for any caller that doesn't set max_concurrency.
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
        """`drain()` instead of `close()` - the latter disconnects immediately,
        without cleanly unsubscribing active subscriptions server-side. For a
        durable JetStream consumer (see `subscribe()`), the binding therefore
        briefly persists server-side even after the process (e.g. via
        `docker compose stop`) has already terminated - an immediate restart
        with the same `durable` name (e.g. the next test run against the same
        service) can then fail with "consumer is already bound to a
        subscription" (observed live in P15-S1 in
        `scripts/run-tests.sh --build`). `drain()` explicitly unsubscribes
        every subscription before the connection is closed, and fixes the
        race at its root - with a built-in timeout (nats-py default 30s), no
        risk of a hanging shutdown."""
        if self._nc is not None and not self._nc.is_closed:
            await self._nc.drain()
