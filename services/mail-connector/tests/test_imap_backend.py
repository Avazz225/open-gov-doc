"""Tests für `ImapBackend` (P24-S3).

`mailpit` (Stand v1.30.6, siehe `docker run axllent/mailpit --help` - kein
`--imap`-Flag existiert) bringt anders als beim POP3-Server, gegen den
`Pop3Backend` bereits real getestet wird, KEINEN eigenen IMAP-Server mit. Es
gibt in diesem Projekt (Stand P24-S3) keinen "echten Dienst" gegen den
getestet werden könnte, ohne `infra/docker-compose.yml` um einen neuen,
IMAP-fähigen Container zu erweitern - außerhalb des Zuschnitts dieser
Session (siehe ADR 0093). Es wird deshalb an der `imaplib`-Grenze gemockt,
mit einer Fake-Implementierung, die exakt die von `ImapBackend` genutzten
Aufrufe (`login`/`status`/`select`/`uid("search", ...)`/`uid("fetch", ...)`/
`close`/`logout`) samt ihrer realen `imaplib`-Antwortformen nachbildet."""

from datetime import UTC, datetime

import mail_connector.backends.imap_backend as imap_backend_module
import pytest
from mail_connector import repository
from mail_connector.backends.imap_backend import ImapBackend


class _FakeImap4:
    """Bildet exakt die von `ImapBackend` genutzte Teilmenge von `imaplib`s
    Antwortformen nach (RFC 3501) - `uid("fetch", ...)`s Antwort ist
    insbesondere die reale, etwas unhandliche Tupel-in-Liste-Form."""

    def __init__(self, messages: dict[bytes, bytes], *, uidvalidity: bytes = b"7") -> None:
        self.messages = messages
        self.uidvalidity = uidvalidity
        self.logged_in = False
        self.selected: str | None = None
        self.closed = False
        self.logged_out = False

    def login(self, username: str, password: str):
        self.logged_in = True
        return "OK", [b"Logged in"]

    def status(self, mailbox: str, what: str):
        return "OK", [f"{mailbox} (UIDVALIDITY {self.uidvalidity.decode()})".encode()]

    def select(self, mailbox: str, readonly: bool = False):
        self.selected = mailbox
        assert readonly is True  # ImapBackend darf das Postfach nicht verändern
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command: str, *args):
        if command == "search":
            ordered = sorted(self.messages, key=lambda u: int(u))
            return "OK", [b" ".join(ordered)]
        if command == "fetch":
            uid = args[0]
            raw = self.messages[uid]
            header = f"{uid.decode()} (UID {uid.decode()} BODY[] {{{len(raw)}}}".encode()
            return "OK", [(header, raw), b")"]
        raise AssertionError(f"unerwarteter uid()-Befehl: {command!r}")

    def close(self):
        self.closed = True

    def logout(self):
        self.logged_out = True


def _patch_imap4(monkeypatch, fake: _FakeImap4) -> None:
    def factory(host, port):
        return fake

    monkeypatch.setattr(imap_backend_module.imaplib, "IMAP4", factory)
    monkeypatch.setattr(imap_backend_module.imaplib, "IMAP4_SSL", factory)


async def test_fetch_new_messages_returns_stable_composite_uid(monkeypatch):
    fake = _FakeImap4({b"101": b"Nachricht eins", b"102": b"Nachricht zwei"}, uidvalidity=b"7")
    _patch_imap4(monkeypatch, fake)
    backend = ImapBackend("irrelevant-host", 143, "user", "pass", use_tls=False, mailbox="INBOX")

    messages = await backend.fetch_new_messages()

    assert {m.uid: m.raw_bytes for m in messages} == {
        "7:101": b"Nachricht eins",
        "7:102": b"Nachricht zwei",
    }
    assert fake.logged_in
    assert fake.selected == "INBOX"
    assert fake.closed
    assert fake.logged_out


async def test_fetch_new_messages_on_empty_mailbox_returns_empty_list(monkeypatch):
    fake = _FakeImap4({})
    _patch_imap4(monkeypatch, fake)
    backend = ImapBackend("irrelevant-host", 143, "user", "pass", use_tls=False)

    assert await backend.fetch_new_messages() == []


async def test_repeated_poll_tick_returns_same_uids_backend_does_not_delete(monkeypatch):
    """`ImapBackend` löscht/markiert nichts serverseitig (`BODY.PEEK[]`,
    kein `\\Seen`/kein `STORE ... \\Deleted`) - ein wiederholter Poll-Tick
    sieht dieselben Nachrichten erneut, exakt wie bei POP3s `UIDL`. Die
    eigentliche Idempotenz kommt vom Aufrufer (`repository.get_by_source_uid`,
    siehe `_poll_loop`), nicht vom Backend selbst."""
    fake = _FakeImap4({b"101": b"Nachricht eins"})
    _patch_imap4(monkeypatch, fake)
    backend = ImapBackend("irrelevant-host", 143, "user", "pass", use_tls=False)

    first_tick = await backend.fetch_new_messages()
    second_tick = await backend.fetch_new_messages()

    assert [m.uid for m in first_tick] == [m.uid for m in second_tick] == ["7:101"]


async def test_dedup_contract_matches_pop3_via_repository(monkeypatch, session):
    """Exerziert den in `interface.py`s `RawIncomingMessage`-Docstring
    beschriebenen Idempotenz-Vertrag Ende-zu-Ende: eine erste Ingestion legt
    die `source_uid` an, `repository.get_by_source_uid` erkennt sie beim
    (simulierten) zweiten Poll-Tick als bereits verarbeitet - exakt dasselbe
    Verhalten, das `_poll_loop` für POP3 UND IMAP gleichermaßen nutzt."""
    fake = _FakeImap4({b"101": b"Nachricht eins"})
    _patch_imap4(monkeypatch, fake)
    backend = ImapBackend("irrelevant-host", 143, "user", "pass", use_tls=False)

    [raw] = await backend.fetch_new_messages()
    assert await repository.get_by_source_uid(session, raw.uid) is None

    await repository.create_inbound_message(
        session,
        source_uid=raw.uid,
        from_address="buerger@example.com",
        subject="Test",
        body_text="Hallo",
        received_at=datetime.now(UTC),
        match_type=None,
        match_value=None,
        proposed_target_type=None,
        proposed_target_id=None,
        match_candidates=[],
    )
    await session.commit()

    [raw_again] = await backend.fetch_new_messages()
    assert raw_again.uid == raw.uid
    assert await repository.get_by_source_uid(session, raw_again.uid) is not None


@pytest.mark.parametrize("use_tls", [True, False])
async def test_use_tls_selects_ssl_or_plain_imap_class(monkeypatch, use_tls):
    calls: list[str] = []

    class _RecordingFake(_FakeImap4):
        pass

    fake = _RecordingFake({})

    def ssl_factory(host, port):
        calls.append("ssl")
        return fake

    def plain_factory(host, port):
        calls.append("plain")
        return fake

    monkeypatch.setattr(imap_backend_module.imaplib, "IMAP4_SSL", ssl_factory)
    monkeypatch.setattr(imap_backend_module.imaplib, "IMAP4", plain_factory)
    backend = ImapBackend("host", 993, "user", "pass", use_tls=use_tls)

    await backend.fetch_new_messages()

    assert calls == (["ssl"] if use_tls else ["plain"])
