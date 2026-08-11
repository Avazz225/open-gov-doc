from mail_connector.backends.interface import MailboxBackend, RawIncomingMessage
from mail_connector.backends.pop3_backend import Pop3Backend
from mail_connector.settings import Settings


def build_backend(settings: Settings) -> MailboxBackend:
    if settings.inbound_protocol == "pop3":
        return Pop3Backend(
            settings.pop3_host,
            settings.pop3_port,
            settings.pop3_username,
            settings.pop3_password,
            use_tls=settings.pop3_use_tls,
        )
    raise ValueError(f"Unbekanntes Posteingang-Protokoll: {settings.inbound_protocol!r}")


__all__ = ["MailboxBackend", "Pop3Backend", "RawIncomingMessage", "build_backend"]
