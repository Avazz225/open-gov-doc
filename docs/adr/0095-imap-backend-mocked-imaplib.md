# 0095 — IMAP-Backend: Test gegen gemocktes `imaplib`, nicht gegen einen echten IMAP-Server

**Status:** akzeptiert (Post-Roadmap Phase 24 Session 3)
**Kontext:** P24-S3, betrifft `mail-connector`

## Entscheidung

`ImapBackend` (`backends/imap_backend.py`) wird in `tests/test_imap_backend.py` gegen eine an der
`imaplib`-Grenze gemockte Fake-Implementierung getestet, NICHT gegen einen echten, im Compose-Stack
laufenden IMAP-Server — abweichend von der projektweit sonst durchgehend gelebten Konvention "gegen den
echten Nachbar-Dienst testen, kein Mocking" (siehe `Pop3Backend`, das bereits gegen den echten
`mailpit`-Container getestet wird, und `docs/services/mail-connector.md`s Tests-Sektion).

## Begründung

- **`mailpit` (der in diesem Projekt als Dev-Mailserver etablierte Container, Stand `v1.30.6`) hat
  keinen IMAP-Server.** Verifiziert über `docker run --rm axllent/mailpit:v1.30.6 --help`: die Ausgabe
  listet `--pop3`/`--pop3-auth-file`/`--pop3-tls-*` (den bereits von `Pop3Backend` genutzten Server),
  aber keinerlei `--imap*`-Flag. Anders als beim POP3-Fall (mailpit v1.15 ergänzte einen POP3-Server
  genau für diesen Selbst-Loopback-Zweck) gibt es hier keinen strukturellen Gegenpart.
- **Ein neuer, IMAP-fähiger Container wäre eine Änderung an `infra/docker-compose.yml`** — dieser
  Session-Zuschnitt (P24-S3, eine von vier parallel arbeitenden Phase-24-Sessions) ist explizit auf
  `services/mail-connector/` und dessen eigene Doku-Datei begrenzt, um Merge-Konflikte zwischen den vier
  parallelen Sessions zu vermeiden. Einen dritten Mail-Test-Container (z. B. `greenmail`/`dovecot`)
  dauerhaft ins gemeinsame Compose-Setup einzuführen, gehört nicht in eine einzelne, isoliert
  überprüfbare Session dieses Zuschnitts.
- **Kein bestehendes Muster für Test-lokale Ad-hoc-Container**: keine der `conftest.py`-Dateien in
  diesem Projekt startet eigenständig Docker-Container aus einer Pytest-Fixture heraus (Stichprobe über
  alle `services/*/tests/conftest.py`) — ein solches Muster hier neu einzuführen, nur für einen
  einzelnen Test, wäre eine eigene, nicht triviale Testinfrastruktur-Entscheidung.
- Die Fake-Implementierung bildet die tatsächlich von `ImapBackend` genutzte `imaplib`-Teilmenge
  (`login`/`status`/`select(..., readonly=True)`/`uid("search", ...)`/`uid("fetch", ...)`/`close`/
  `logout`) exakt in ihrer realen RFC-3501-Antwortform nach (insbesondere `uid("fetch", ...)`s
  Tupel-in-Liste-Struktur), nicht nur ein generisches Duck-Typing — reduziert das Risiko, dass der Mock
  ein Verhalten vortäuscht, das ein echter Server nicht hätte.

## Konsequenzen

- Die Tests beweisen, dass `ImapBackend` korrekt mit `imaplib`s Antwortformen umgeht (Parsing von
  `UIDVALIDITY`/UID-Listen/Fetch-Tupeln, `BODY.PEEK[]` statt `RFC822` zur Vermeidung von
  `\Seen`-Seiteneffekten, `readonly=True` beim `select`) — sie beweisen NICHT, dass ein bestimmter
  echter IMAP-Server (Dovecot, Exchange, Gmail, ...) exakt dieselben Antwortformen liefert. Für
  `poplib`/`Pop3Backend` entfällt dieses Restrisiko, weil dort echt gegen `mailpit` getestet wird.
- Die Live-Verifikation dieser Session (siehe PROGRESS.md) schließt diese Lücke bestmöglich: ein
  temporärer, NICHT in `infra/docker-compose.yml` eingetragener `greenmail`-Container (nur für die Dauer
  der manuellen Verifikation, danach entfernt) bestätigt den vollständigen Empfangspfad gegen einen
  echten IMAP-Server.
- **Offener Punkt für eine spätere Session**: sollte `mail-connector` produktiv gegen IMAP betrieben
  werden, wäre ein dauerhafter IMAP-Testserver im Compose-Stack (analog zu mailpits POP3-Server) eine
  sinnvolle Ergänzung — dann ließe sich `test_imap_backend.py` durch echte End-zu-Ende-Tests ersetzen
  oder ergänzen, exakt wie bei `Pop3Backend`.
