# 0001 — Event-Bus-Konsumenten besitzen keinen eigenen Stream

**Status:** akzeptiert
**Kontext:** Konzept 3.4/5.3, Session P1-S2 (Audit Service)

## Entscheidung

`NatsEventBusClient` (libs/dms-eventbus-client) unterscheidet zwei Rollen über
den neuen Konstruktor-Parameter `ensure_stream`:

- **Producer** (Default, `ensure_stream=True`, `stream=<name>` erforderlich):
  `connect()` legt den eigenen JetStream-Stream an, falls er noch nicht existiert.
- **Reine Konsumenten** (`ensure_stream=False`, kein `stream`-Name nötig):
  `connect()` verbindet nur, ohne einen Stream zu deklarieren. `subscribe()`
  funktioniert trotzdem, da JetStream den passenden Stream serverseitig anhand
  des abonnierten Subjects auflöst.

## Begründung

Der Audit Service (3.4/5.3) soll Ereignisse **beliebig vieler** Producer-Services
konsumieren, ohne deren Streams zu kennen oder zu besitzen. Mit der ursprünglichen
Signatur aus P0-S2 (`stream` war Pflichtparameter, `connect()` erzeugte ihn immer)
hätte der Audit Service für jeden Producer einen eigenen `NatsEventBusClient` mit
dessen Stream-Namen instanziieren müssen - unnötige Kopplung an Producer-interne
Namensgebung, die bei jedem neuen Service-Typ hätte nachgezogen werden müssen.

## Konsequenzen

- Konsumenten kennen nur die Subject-Konvention (`<producer-stream>.>`), nicht die
  Stream-Namen selbst.
- Ein Subject kann nur konsumiert werden, wenn mindestens ein Producer den
  zugehörigen Stream bereits angelegt hat (Producer muss vor dem ersten
  Konsumieren mindestens einmal gestartet worden sein - unkritisch, da Streams
  serverseitig persistent sind und nicht bei jedem Producer-Neustart neu entstehen).
- Bestehender Producer-Code (Registry Service, zukünftige Services) ist von der
  Änderung nicht betroffen - `ensure_stream=True` bleibt Default.
