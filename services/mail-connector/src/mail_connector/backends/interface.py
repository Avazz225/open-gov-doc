from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RawIncomingMessage:
    """An unprocessed message retrieved from the backend - `uid` is the
    stable, backend-native identifier (POP3 UIDL) by which a repeated poll
    tick recognizes already-processed messages (see
    `repository.get_by_source_uid`)."""

    uid: str
    raw_bytes: bytes


class MailboxBackend(ABC):
    """Uniform interface for inbox-retrieval plugins (2.5/3.3), following
    the same "add-alongside" principle as the storage backends (3.6) and
    the virus-scan engines (10.3, ADR 0010): a new protocol (e.g. IMAP,
    Microsoft Graph for Exchange/O365) only implements this interface, the
    rest of the service remains unchanged."""

    @abstractmethod
    async def fetch_new_messages(self) -> list[RawIncomingMessage]: ...
