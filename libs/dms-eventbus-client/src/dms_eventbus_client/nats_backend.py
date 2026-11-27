import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

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


# Dead letters (P99-S1, ADR 0270). A handler that keeps failing used to be
# redelivered forever and stalled its consumer (Phase 51, P98-S1). Now each
# failure is `nak`ed with a growing delay; after `max_deliver` deliveries the
# message is copied to the DLQ stream and acknowledged, so the consumer moves
# on. The limit is enforced here (from `num_delivered`), not by the server:
# the server keeps redelivering, so this code always sees the last attempt -
# and existing durables need no migration.
DLQ_STREAM = "DLQ"
DLQ_SUBJECT_PREFIX = "dlq"
DEFAULT_MAX_DELIVER = int(os.environ.get("DMS_EVENTBUS_MAX_DELIVER", "12"))
DLQ_MAX_AGE_SECONDS = int(os.environ.get("DMS_EVENTBUS_DLQ_MAX_AGE_DAYS", "90")) * 86400
MAX_RETRY_DELAY_SECONDS = 300.0
_ERROR_TEXT_LIMIT = 500


def retry_delay(num_delivered: int) -> float:
    """1, 2, 4, ... seconds, at most 5 minutes: with 12 deliveries a message
    gets about 18 minutes to succeed (a restarting dependency, a short outage)
    before it is dead-lettered."""
    return min(2.0 ** max(num_delivered - 1, 0), MAX_RETRY_DELAY_SECONDS)


def _subject_token(name: str) -> str:
    # A subject token may not contain dots, spaces or wildcards.
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "_"


def dead_letter_subject(stream: str, consumer: str) -> str:
    return f"{DLQ_SUBJECT_PREFIX}.{_subject_token(stream)}.{_subject_token(consumer)}"


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

    def __init__(
        self,
        url: str,
        stream: str | None = None,
        *,
        ensure_stream: bool = True,
        max_deliver: int | None = None,
    ) -> None:
        if ensure_stream and stream is None:
            raise ValueError("stream ist erforderlich, wenn ensure_stream=True")
        self._url = url
        self._stream = stream
        self._ensure_stream = ensure_stream
        self._max_deliver = max(1, max_deliver or DEFAULT_MAX_DELIVER)
        self._dlq_ready = False
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
            except Exception as exc:
                # Retry with a delay, or dead-letter after the last attempt
                # (P99-S1) - the same as the sequential path.
                await self._on_handler_failure(msg, exc, subject, _ack)
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
                # Sequential (default): one message at a time, acked when done.
                try:
                    await _handle(msg)
                except Exception as exc:
                    await self._on_handler_failure(msg, exc, subject, _ack)
                    return
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

    async def _on_handler_failure(self, msg, exc: Exception, subject: str, ack) -> None:
        """A failed handler: `nak` with a growing delay, or - at the last
        attempt - copy the message to the DLQ and acknowledge it (P99-S1).
        If even the DLQ can't take it, `nak` with the longest delay: nothing
        is dropped."""
        try:
            meta = msg.metadata
        except Exception:  # noqa: BLE001 - not a JetStream message
            meta = None
        deliveries = int(getattr(meta, "num_delivered", 1) or 1)
        if deliveries < self._max_deliver:
            delay = retry_delay(deliveries)
            logger.warning(
                "eventbus_handler_failed_retrying",
                exc_info=exc,
                extra={
                    "subject": subject,
                    "deliveries": deliveries,
                    "max_deliver": self._max_deliver,
                    "retry_in_seconds": delay,
                },
            )
            await self._settle(msg.nak(delay=delay), subject)
            return
        try:
            dlq_subject = await self._dead_letter(msg, exc, meta, deliveries)
        except Exception:
            logger.exception("eventbus_dead_letter_failed", extra={"subject": subject})
            await self._settle(msg.nak(delay=MAX_RETRY_DELAY_SECONDS), subject)
            return
        logger.error(
            "eventbus_message_dead_lettered",
            exc_info=exc,
            extra={
                "subject": subject,
                "deliveries": deliveries,
                "dead_letter_subject": dlq_subject,
            },
        )
        await ack(msg)

    async def _settle(self, awaitable, subject: str) -> None:
        try:
            await awaitable
        except Exception:  # noqa: BLE001 - redelivery happens anyway after ack_wait
            logger.warning("eventbus_nak_failed", extra={"subject": subject}, exc_info=True)

    async def _ensure_dlq(self) -> None:
        if self._dlq_ready:
            return
        try:
            await self._js.stream_info(DLQ_STREAM)
        except NotFoundError:
            try:
                await self._js.add_stream(
                    name=DLQ_STREAM,
                    subjects=[f"{DLQ_SUBJECT_PREFIX}.>"],
                    max_age=DLQ_MAX_AGE_SECONDS,
                )
            except Exception:  # noqa: BLE001 - another instance created it first
                await self._js.stream_info(DLQ_STREAM)
        self._dlq_ready = True

    async def _dead_letter(self, msg, exc: Exception, meta, deliveries: int) -> str:
        """Copies the message to `dlq.<stream>.<consumer>`: the original
        payload and headers, plus where it came from and why it failed."""
        await self._ensure_dlq()
        stream = getattr(meta, "stream", None) or "unknown"
        consumer = getattr(meta, "consumer", None) or "unknown"
        sequence = getattr(getattr(meta, "sequence", None), "stream", None)
        error = f"{type(exc).__name__}: {exc}"[:_ERROR_TEXT_LIMIT]
        headers = dict(msg.headers or {})
        headers.update(
            {
                "Dms-Dlq-Subject": msg.subject,
                "Dms-Dlq-Stream": stream,
                "Dms-Dlq-Stream-Seq": str(sequence or ""),
                "Dms-Dlq-Consumer": consumer,
                "Dms-Dlq-Deliveries": str(deliveries),
                "Dms-Dlq-Error": error.replace("\n", " ").replace("\r", " "),
                "Dms-Dlq-Failed-At": datetime.now(UTC).isoformat(),
            }
        )
        subject = dead_letter_subject(stream, consumer)
        await self._js.publish(subject, msg.data, headers=headers)
        return subject

    async def dead_letter_counts(self) -> dict[tuple[str, str], int]:
        """Dead letters waiting, per (stream, consumer) - for the
        `eventbus.dead_letters` sensor. Empty when there is no DLQ yet."""
        try:
            info = await self._js.stream_info(DLQ_STREAM, subjects_filter=f"{DLQ_SUBJECT_PREFIX}.>")
        except NotFoundError:
            return {}
        counts: dict[tuple[str, str], int] = {}
        for subject, count in (info.state.subjects or {}).items():
            parts = subject.split(".", 2)
            if len(parts) == 3:
                counts[(parts[1], parts[2])] = int(count)
        return counts

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
