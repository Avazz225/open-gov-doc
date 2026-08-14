from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

MessageHandler = Callable[[bytes], Awaitable[None]]


class SubjectNotFoundError(Exception):
    """No stream covers the subscribed subject (no producer started yet,
    see ADR 0001) - backend-independent, so callers don't have to code
    against NATS-specific exceptions."""


class EventBusClient(ABC):
    """Publish/consume interface, independent of the concrete bus (concept 3.4).

    Services know only this interface - which backend (NATS JetStream by
    default, Kafka for large installations) actually runs is purely a
    deployment decision.
    """

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def publish(self, subject: str, payload: bytes) -> None: ...

    @abstractmethod
    async def subscribe(
        self,
        subject: str,
        handler: MessageHandler,
        *,
        durable: str,
        deliver_new: bool = False,
        max_concurrency: int | Callable[[], Awaitable[int]] = 1,
    ) -> None:
        """``deliver_new=True`` delivers only messages from now on, instead of the
        complete history of the stream (default) - for consumers like the audit
        service, which should catch up gaplessly even after a restart, ``False``
        remains correct; short-lived/test subscriptions set ``deliver_new=True``.

        ``max_concurrency`` (P5b-S5, ocr-service "processing batch size"):
        default ``1`` behaves exactly as before - a callback is fully processed
        (including ack) before the next one is handled. A value > 1 (or an
        async callable that re-reads the current value live, e.g. from an
        admin-UI-editable setting) allows up to that many handler calls to run
        concurrently; a failing handler is, as in the sequential case,
        **not** acknowledged (redelivery).
        """
        ...

    @abstractmethod
    async def close(self) -> None: ...
