from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

MessageHandler = Callable[[bytes], Awaitable[None]]


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
        self, subject: str, handler: MessageHandler, *, durable: str, deliver_new: bool = False
    ) -> None:
        """``deliver_new=True`` liefert nur Nachrichten ab jetzt, statt den kompletten
        Verlauf des Streams (Default) - für Konsumenten wie den Audit Service, die
        auch nach einem Neustart lückenlos aufholen sollen, bleibt ``False`` richtig;
        kurzlebige/testweise Abonnements setzen ``deliver_new=True``.
        """
        ...

    @abstractmethod
    async def close(self) -> None: ...
