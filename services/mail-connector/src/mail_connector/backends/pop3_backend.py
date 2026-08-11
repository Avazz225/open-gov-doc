import asyncio
import contextlib
import poplib

from mail_connector.backends.interface import MailboxBackend, RawIncomingMessage


class Pop3Backend(MailboxBackend):
    """Echte, standardprotokoll-basierte Abholung über POP3 (`poplib`,
    Standardbibliothek - kein zusätzliches Paket nötig). Im Entwicklungsbetrieb
    gegen den bereits vorhandenen `mailpit`-Container gerichtet, dessen
    eigener POP3-Server (`--pop3`/`--pop3-auth-file`, seit mailpit v1.15) exakt
    dieselben abgefangenen Nachrichten zurückliefert, die zuvor über SMTP
    eingeliefert wurden - ein Selbst-Loopback-Test ohne externe Infrastruktur,
    gleiches Prinzip wie der Federation-Hub-Selbst-Loopback (P6-S9). Gegen
    einen echten Mailserver identisch nutzbar, nur mit anderen `host`/`port`/
    Zugangsdaten.

    `poplib` ist synchron/blockierend - jeder Aufruf läuft über
    `asyncio.to_thread`, damit ein langsamer/hängender POP3-Server nicht den
    gesamten Event-Loop blockiert."""

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
            # UIDL liefert einen über Sitzungen hinweg stabilen Bezeichner je
            # Nachricht (RFC 1939) - Grundlage für die Idempotenz-Prüfung in
            # `repository.get_by_source_uid`, ohne die Nachricht serverseitig
            # löschen zu müssen (kein `client.dele()` hier, bewusst - ein
            # Betreiber kann dieselbe Mailbox parallel noch anderweitig
            # einsehen wollen).
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
