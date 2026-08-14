import asyncio
import contextlib
import imaplib
import re

from mail_connector.backends.interface import MailboxBackend, RawIncomingMessage

_UIDVALIDITY_RE = re.compile(rb"UIDVALIDITY (\d+)")


class ImapBackend(MailboxBackend):
    """Real, standard-protocol-based retrieval via IMAP (`imaplib`, standard
    library - no additional package needed), second implementation of the
    plugin pattern already envisioned in `interface.py`'s docstring
    alongside `Pop3Backend`. Unlike POP3, IMAP organizes a mailbox into
    named folders (`mailbox`, default `INBOX`) - which folder is fetched
    is therefore configurable (`DMS_IMAP_MAILBOX`).

    Deliberately uses the UID-based command set (`UID SEARCH`/`UID FETCH`,
    RFC 3501 §6.4.8), NOT the sequence-number-based one (`SEARCH`/`FETCH`):
    sequence numbers shift with every deletion/access within the same
    session, UIDs are stable across sessions - the same requirement as
    POP3's `UIDL` (see `pop3_backend.py`).

    A plain IMAP UID, however, is only stable WITHIN the same
    `UIDVALIDITY` epoch (RFC 3501 §2.3.1.1) - if a server rebuilds the
    mailbox (a rare special case), it may assign the same UID number to a
    DIFFERENT message. The `RawIncomingMessage.uid` passed on to
    `repository.get_by_source_uid` is therefore a composite identifier
    `f"{uidvalidity}:{uid}"` instead of the bare UID - a later UIDVALIDITY
    change thus manifests as a (deliberately intended) re-ingestion of all
    messages, instead of silently skipping a wrong message as already
    processed (or vice versa).

    `BODY.PEEK[]` instead of `RFC822`/`BODY[]` on fetch - the latter
    implicitly mark the message as `\\Seen`, which is undesirable here
    (same reasoning as POP3's deliberately omitted `client.dele()`: an
    operator may want to view the same mailbox in parallel elsewhere as
    well). `select` additionally runs with `readonly=True`, as a second
    safeguard against server-side side effects.

    `imaplib`, like `poplib`, is synchronous/blocking - every call runs
    via `asyncio.to_thread`, so a slow/hanging IMAP server does not block
    the entire event loop."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        use_tls: bool,
        mailbox: str = "INBOX",
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._mailbox = mailbox

    def _fetch_sync(self) -> list[RawIncomingMessage]:
        client_cls = imaplib.IMAP4_SSL if self._use_tls else imaplib.IMAP4
        client = client_cls(self._host, self._port)
        try:
            client.login(self._username, self._password)

            status, data = client.status(self._mailbox, "(UIDVALIDITY)")
            if status != "OK" or not data or data[0] is None:
                raise RuntimeError(
                    f"IMAP-Postfach {self._mailbox!r} - UIDVALIDITY konnte nicht ermittelt werden"
                )
            match = _UIDVALIDITY_RE.search(data[0])
            uidvalidity = match.group(1).decode("ascii") if match else "0"

            status, _ = client.select(self._mailbox, readonly=True)
            if status != "OK":
                raise RuntimeError(
                    f"IMAP-Postfach {self._mailbox!r} konnte nicht selektiert werden"
                )

            status, uid_data = client.uid("search", None, "ALL")
            if status != "OK":
                raise RuntimeError(f"IMAP UID SEARCH in {self._mailbox!r} fehlgeschlagen")
            uids = uid_data[0].split() if uid_data and uid_data[0] else []

            messages: list[RawIncomingMessage] = []
            for uid in uids:
                status, msg_data = client.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK" or not msg_data or msg_data[0] is None:
                    continue
                raw_bytes = msg_data[0][1]
                messages.append(
                    RawIncomingMessage(
                        uid=f"{uidvalidity}:{uid.decode('ascii')}", raw_bytes=raw_bytes
                    )
                )
            return messages
        finally:
            with contextlib.suppress(Exception):
                client.close()
            with contextlib.suppress(Exception):
                client.logout()

    async def fetch_new_messages(self) -> list[RawIncomingMessage]:
        return await asyncio.to_thread(self._fetch_sync)
