from mail_connector.backends.imap_backend import ImapBackend
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
    if settings.inbound_protocol == "imap":
        return ImapBackend(
            settings.imap_host,
            settings.imap_port,
            settings.imap_username,
            settings.imap_password,
            use_tls=settings.imap_use_tls,
            mailbox=settings.imap_mailbox,
        )
    raise ValueError(f"Unbekanntes Posteingang-Protokoll: {settings.inbound_protocol!r}")


__all__ = ["ImapBackend", "MailboxBackend", "Pop3Backend", "RawIncomingMessage", "build_backend"]
