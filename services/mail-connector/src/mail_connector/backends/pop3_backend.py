import asyncio
import contextlib
import poplib

from mail_connector.backends.interface import MailboxBackend, RawIncomingMessage


class Pop3Backend(MailboxBackend):
    """Real, standard-protocol-based retrieval via POP3 (`poplib`,
    standard library - no additional package needed). In development this
    targets the already-existing `mailpit` container, whose own POP3 server
    (`--pop3`/`--pop3-auth-file`, since mailpit v1.15) returns exactly the
    same intercepted messages that were previously submitted via SMTP - a
    self-loopback test without external infrastructure, same principle as
    the federation hub self-loopback (P6-S9). Usable identically against a
    real mail server, just with different `host`/`port`/credentials.

    `poplib` is synchronous/blocking - every call runs via
    `asyncio.to_thread` so that a slow/hanging POP3 server doesn't block the
    entire event loop."""

    def __init__(
        self, host: str, port: int, username: str, password: str, *, use_tls: bool
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls

    def _fetch_sync(self) -> list[RawIncomingMessage]:
        client_cls = poplib.POP3_SSL if self._use_tls else poplib.POP3
        client = client_cls(self._host, self._port)
        try:
            client.user(self._username)
            client.pass_(self._password)
            # UIDL provides an identifier for each message that is stable
            # across sessions (RFC 1939) - basis for the idempotency check
            # in `repository.get_by_source_uid`, without having to delete
            # the message server-side (no `client.dele()` here, deliberately
            # - an operator may still want to view the same mailbox in
            # parallel via other means).
            _, uidl_lines, _ = client.uidl()
            messages: list[RawIncomingMessage] = []
            for line in uidl_lines:
                number_str, uid = line.decode("ascii").split(" ", 1)
                _, raw_lines, _ = client.retr(int(number_str))
                raw_bytes = b"\r\n".join(raw_lines)
                messages.append(RawIncomingMessage(uid=uid, raw_bytes=raw_bytes))
            return messages
        finally:
            with contextlib.suppress(Exception):
                client.quit()

    async def fetch_new_messages(self) -> list[RawIncomingMessage]:
        return await asyncio.to_thread(self._fetch_sync)
