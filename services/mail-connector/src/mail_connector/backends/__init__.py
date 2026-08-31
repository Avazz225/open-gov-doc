from mail_connector.backends.imap_backend import ImapBackend
from mail_connector.backends.interface import MailboxBackend, RawIncomingMessage
from mail_connector.backends.pop3_backend import Pop3Backend
from mail_connector.settings import MailboxConfig


def build_backend(mailbox: MailboxConfig) -> MailboxBackend:
    """Since Post-Roadmap Phase 31 Session 12a, one backend instance per
    configured `MailboxConfig` (previously one per `Settings`, service-wide)
    - `main.py`'s lifespan builds a `dict[mailbox_id, MailboxBackend]`,
    reused across poll ticks (both `Pop3Backend`/`ImapBackend` connect fresh
    on every `fetch_new_messages()` call, no persistent connection state to
    worry about)."""
    if mailbox.inbound_protocol == "pop3":
        return Pop3Backend(
            mailbox.pop3_host,
            mailbox.pop3_port,
            mailbox.pop3_username,
            mailbox.pop3_password,
            use_tls=mailbox.pop3_use_tls,
        )
    if mailbox.inbound_protocol == "imap":
        return ImapBackend(
            mailbox.imap_host,
            mailbox.imap_port,
            mailbox.imap_username,
            mailbox.imap_password,
            use_tls=mailbox.imap_use_tls,
            mailbox=mailbox.imap_mailbox,
        )
    raise ValueError(f"Unbekanntes Posteingang-Protokoll: {mailbox.inbound_protocol!r}")


__all__ = ["ImapBackend", "MailboxBackend", "Pop3Backend", "RawIncomingMessage", "build_backend"]
