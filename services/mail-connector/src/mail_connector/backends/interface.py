from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RawIncomingMessage:
    """Eine unverarbeitete, vom Backend abgerufene Nachricht - `uid` ist der
    stabile, backend-eigene Bezeichner (POP3-UIDL), über den ein wiederholter
    Poll-Tick bereits verarbeitete Nachrichten erkennt (siehe
    `repository.get_by_source_uid`)."""

    uid: str
    raw_bytes: bytes


class MailboxBackend(ABC):
    """Einheitliches Interface für Posteingang-Abruf-Plugins (2.5/3.3),
    nach demselben "Dazustellen"-Prinzip wie die Storage-Backends (3.6) und
    die Virenscan-Engines (10.3, ADR 0010): ein neues Protokoll (z. B. IMAP,
    Microsoft Graph für Exchange/O365) implementiert nur dieses Interface,
    der Rest des Service bleibt unverändert."""

    @abstractmethod
    async def fetch_new_messages(self) -> list[RawIncomingMessage]: ...
