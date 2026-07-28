from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

MessageHandler = Callable[[bytes], Awaitable[None]]


class SubjectNotFoundError(Exception):
    """Kein Stream deckt das abonnierte Subject ab (noch kein Producer
    gestartet, siehe ADR 0001) - Backend-unabhängig, damit Aufrufer nicht
    gegen NATS-spezifische Exceptions programmieren müssen."""


class EventBusClient(ABC):
    """Publish/Consume-Interface, unabhängig vom konkreten Bus (Konzept 3.4).

    Services kennen nur diese Schnittstelle - welches Backend (NATS JetStream
    default, Kafka für große Installationen) tatsächlich läuft, ist reine
    Deployment-Entscheidung.
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
        """``deliver_new=True`` liefert nur Nachrichten ab jetzt, statt den kompletten
        Verlauf des Streams (Default) - für Konsumenten wie den Audit Service, die
        auch nach einem Neustart lückenlos aufholen sollen, bleibt ``False`` richtig;
        kurzlebige/testweise Abonnements setzen ``deliver_new=True``.

        ``max_concurrency`` (P5b-S5, ocr-service "Verarbeitungs-Batch-Size"):
        Default ``1`` verhält sich exakt wie zuvor - ein Callback wird komplett
        abgearbeitet (inkl. Ack) bevor der nächste verarbeitet wird. Ein Wert
        > 1 (oder ein async Callable, das den aktuellen Wert live nachliest,
        z. B. aus einer Admin-UI-editierbaren Einstellung) lässt bis zu so viele
        Handler-Aufrufe gleichzeitig laufen; ein fehlschlagender Handler wird
        dabei wie im sequentiellen Fall **nicht** bestätigt (Neuzustellung).
        """
        ...

    @abstractmethod
    async def close(self) -> None: ...
