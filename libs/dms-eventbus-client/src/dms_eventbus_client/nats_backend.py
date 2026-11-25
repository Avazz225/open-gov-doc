import asyncio
import logging
from collections.abc import Awaitable, Callable

import nats
from nats.js import api
from nats.js.client import JetStreamContext
from nats.js.errors import NotFoundError
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind

from dms_eventbus_client.interface import EventBusClient, MessageHandler, SubjectNotFoundError

logger = logging.getLogger(__name__)

# Trace propagation through NATS headers (P94-S1, ADR 0250): a published
# message carries the W3C `traceparent` of the span that published it, and the
# consumer continues that trace. Uses only the OpenTelemetry API - without a
# configured provider (tracing off) every call here is a no-op.
_tracer = trace.get_tracer("dms_eventbus_client")


def _messaging_attributes(subject: str, operation: str) -> dict[str, str]:
    return {
        "messaging.system": "nats",
        "messaging.destination.name": subject,
        "messaging.operation.type": operation,
    }


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
        with _tracer.start_as_current_span(
            f"{subject} publish",
            kind=SpanKind.PRODUCER,
            attributes=_messaging_attributes(subject, "publish"),
        ):
            headers: dict[str, str] = {}
            propagate.inject(headers)
            await self._js.publish(subject, payload, headers=headers or None)

    async def subscribe(
        self,
        subject: str,
        handler: MessageHandler,
        *,
        durable: str | None,
        deliver_new: bool = False,
        max_concurrency: int | Callable[[], Awaitable[int]] = 1,
    ) -> None:
        assert self._js is not None, "connect() muss vor subscribe() aufgerufen werden"
        # `durable=None` (P91-S2b): an ephemeral consumer the server removes
        # once the subscription is gone - for tests and other short-lived
        # listeners. A durable one outlives its process by design and keeps
        # collecting every new message; random test durables had piled up
        # to 419 consumers with 1.2 million pending messages.

        in_flight = 0
        slot_free = asyncio.Condition()

        async def _resolve_limit() -> int:
            if callable(max_concurrency):
                return await max_concurrency()
            return max_concurrency

        async def _handle(msg) -> None:
            # Continue the publisher's trace (if the message carries one).
            context = propagate.extract(msg.headers or {})
            with _tracer.start_as_current_span(
                f"{msg.subject} process",
                context=context,
                kind=SpanKind.CONSUMER,
                attributes=_messaging_attributes(msg.subject, "process"),
            ):
                await handler(msg.data)

        async def _run(msg) -> None:
            nonlocal in_flight
            try:
                await _handle(msg)
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
                await _ack(msg)
            finally:
                async with slot_free:
                    in_flight -= 1
                    slot_free.notify_all()

        async def _ack(msg) -> None:
            # `msg.ack()` itself can raise (e.g. `MsgAlreadyAckdError` on a
            # redelivery race) - unguarded, that exception would surface
            # only as an orphaned "Task exception was never retrieved" from
            # `_run`'s fire-and-forget task (never logged with the subject,
            # never affecting redelivery either way, since the message is
            # already acked by definition of this error).
            try:
                await msg.ack()
            except Exception:
                logger.exception(
                    "Bestätigung fehlgeschlagen (Subject %r) - Nachricht vermutlich "
                    "bereits anderweitig bestätigt, keine weitere Aktion nötig",
                    subject,
                )

        async def _callback(msg) -> None:
            nonlocal in_flight
            limit = await _resolve_limit()
            if limit <= 1:
                # Exactly the previous behavior (no concurrency) - unchanged
                # for any caller that doesn't set max_concurrency.
                await _handle(msg)
                await _ack(msg)
                return
            async with slot_free:
                while in_flight >= limit:
                    await slot_free.wait()
                in_flight += 1
            asyncio.create_task(_run(msg))

        deliver_policy = api.DeliverPolicy.NEW if deliver_new else None
        try:
            if durable is None:
                await self._js.subscribe(subject, cb=_callback, deliver_policy=deliver_policy)
            else:
                # Queue groups (P95-S2a, ADR 0257): a durable is shared by
                # every instance of the service - each message goes to one of
                # them. Without a group, a second instance (a replica, a
                # worker, a rolling update's surge pod) died at startup with
                # "consumer is already bound to a subscription".
                await self._migrate_to_queue_group(subject, durable)
                await self._js.subscribe(
                    subject,
                    queue=durable,
                    durable=durable,
                    cb=_callback,
                    deliver_policy=deliver_policy,
                )
        except NotFoundError as exc:
            raise SubjectNotFoundError(
                f"Kein Stream deckt Subject {subject!r} ab - noch kein Producer gestartet?"
            ) from exc

    async def _migrate_to_queue_group(self, subject: str, durable: str) -> None:
        """Recreates a durable from before P95-S2a (push, no deliver group) as
        a queue consumer, once, at the position it had reached: the first
        unacknowledged message (`ack_floor + 1`), so nothing is lost; messages
        delivered but not yet acked come again - JetStream's at-least-once,
        the same as after any redelivery. A durable that never delivered
        anything keeps its original start rule. The deliver subject is derived
        from the names, so two instances migrating at the same moment end up
        with the same consumer. Idempotent: a durable with a group is left
        alone."""
        try:
            stream = await self._js.find_stream_name_by_subject(subject)
            info = await self._js.consumer_info(stream, durable)
        except NotFoundError:
            return  # no stream (subscribe reports it) or a new durable
        config = info.config
        if config.deliver_group or not config.deliver_subject:
            return  # already a queue consumer, or a pull consumer
        delivered = info.delivered.stream_seq if info.delivered else 0
        ack_floor = info.ack_floor.stream_seq if info.ack_floor else 0
        if delivered:
            start = {
                "deliver_policy": api.DeliverPolicy.BY_START_SEQUENCE,
                "opt_start_seq": ack_floor + 1,
            }
        else:
            start = {
                "deliver_policy": config.deliver_policy,
                "opt_start_seq": config.opt_start_seq,
                "opt_start_time": config.opt_start_time,
            }
        logger.warning(
            "eventbus_durable_migrated_to_queue_group",
            extra={"stream": stream, "durable": durable, "ack_floor": ack_floor},
        )
        await self._js.delete_consumer(stream, durable)
        try:
            await self._js.add_consumer(
                stream,
                api.ConsumerConfig(
                    durable_name=durable,
                    deliver_group=durable,
                    deliver_subject=f"_DMS.deliver.{stream}.{durable}",
                    filter_subject=config.filter_subject,
                    ack_policy=config.ack_policy,
                    ack_wait=config.ack_wait,
                    max_deliver=config.max_deliver,
                    max_ack_pending=config.max_ack_pending,
                    **start,
                ),
            )
        except Exception:  # noqa: BLE001 - another instance created it first
            await self._js.consumer_info(stream, durable)

    async def consumer_pending(self, stream: str, durable: str) -> int | None:
        """How many messages a durable consumer still has to see (P95-S1f):
        JetStream's `num_pending` plus those delivered but not yet acked.
        `None` when the consumer doesn't exist (yet) or NATS can't say - a
        sensor shows a gap rather than a made-up zero."""
        try:
            info = await self._js.consumer_info(stream, durable)
        except Exception:  # noqa: BLE001 - a sensor must not fail the scrape
            return None
        return int(info.num_pending or 0) + int(info.num_ack_pending or 0)

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
