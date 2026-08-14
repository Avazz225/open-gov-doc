# 0081 — federation-hub-service: Handover-Zustellung Retry/Backoff, `pending_retry`

**Status:** akzeptiert (Session 5 von 7, siehe Phase 20 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 20 Session 5, betrifft `federation-hub-service`

## Entscheidung

`POST /handovers` stellte den vom Ende-zu-Ende verschlüsselten Payload bislang vollständig **synchron im
Request** an `to_installation_id`s Callback-URL zu — ein einzelner Fehlschlag (Netzwerkfehler,
Nicht-2xx-Antwort) setzte sofort `status="delivery_failed"` (terminal), ohne jede Wiederholung. Diese
Session überträgt das aus `notification-service` (ADR 0079) und `ocr-service`/`rendering-service`
(ADR 0080) bekannte Muster auf die `Handover`-Erstzustellung — **nur** auf `POST /handovers`, NICHT auf
die separate, symmetrische `POST /handovers/{id}/result`-Rückleitung (out of scope für diese Session).

1. **Neue Felder** `attempts: int` (Default 0) und `next_retry_at: datetime | None` auf `Handover`.
   Neuer Zwischenstatus **`pending_retry`** (Statusfluss jetzt: `"pending"` → `"delivered"` |
   `"pending_retry"` → ... → `"delivery_failed"` → `"completed"` | `"result_delivery_failed"`) —
   bewusst ein neuer Name für den Zwischenstatus, aber `"delivery_failed"` bleibt als Name des
   **terminalen** (erschöpften) Zustands erhalten, statt wie in ADR 0078–0080 einen neuen
   `..._permanent`-Namen einzuführen (Abweichung ausdrücklich so vom Plan vorgegeben).
2. **Retry-aware Zustellungs-Buchführung**: `repository.mark_handover_delivered` bekommt einen neuen
   Pflichtparameter `max_attempts` — bei Erfolg unverändert `"delivered"`; bei Fehlschlag unterhalb von
   `max_attempts` (Default 5, `settings.max_handover_delivery_attempts`) `status="pending_retry"` mit
   einem per `compute_backoff_seconds` gesetzten `next_retry_at`; erst bei Erschöpfung
   `status="delivery_failed"`. Neue `repository.list_due_for_retry`/`repository.reset_for_retry`
   (identisches Muster wie in ADR 0079/0080).
3. **Neuer, eigenständiger Retry-Poll-Loop** (`_handover_retry_poll_loop`, Intervall 60s wie
   `notification-service`) — der erste Zustellversuch bleibt synchron in `POST /handovers`, nur die
   WIEDERHOLUNG läuft asynchron.
4. **Neuer Endpunkt** `POST /handovers/{id}/retry` — `409` außer bei `delivery_failed`, sonst
   `repository.reset_for_retry` gefolgt von einem sofortigen synchronen Wiederholungsversuch (gleiche
   Begründung wie ADR 0079/0080: ein einzelner HTTP-Zustellschritt, kein mehrphasiger Prozess). Bewusst
   **ohne** RBAC-Gate — `federation-hub-service` hat wie `notification-service` (vor ADR 0079) keine
   `permission-service`-Integration; eine hinzuzufügen wäre Scope-Creep über eine reine
   Resilienz-Session hinaus.
5. **Der architektonische Kernkonflikt dieser Session — "nie persistierter Payload" vs. "Payload für
   späteren Retry nötig"**: `Handover` speichert bewusst **nie** den Ende-zu-Ende verschlüsselten Payload
   selbst (7.4, ADR 0028 "Selbst-Loopback" — der Hub protokolliert nur Vermittlungs-**Metadaten**). Ein
   Retry-Poll-Tick braucht den Payload aber, um ihn erneut zuzustellen. Lösung: ein rein **flüchtiger
   Prozessspeicher-Cache** `app.state.pending_handover_payloads: dict[str, dict]` (keyed by
   `handover_id`), befüllt nur solange ein Handover tatsächlich `pending_retry` ist, geleert bei Erfolg
   oder Erschöpfung. **Kein neues Feld auf `Handover` selbst** — das architektonische Prinzip "kein
   Payload wird je persistiert" bleibt vollständig gültig, nicht stillschweigend umgangen.

## Begründung

- **Warum das notification-service-Muster (ADR 0079) statt des archival-service-Musters (ADR 0078)**:
  `POST /handovers` verarbeitet synchron im HTTP-Request, keine mehrphasige Zustandsmaschine — ein
  Backoff-Warten direkt im Request würde den Aufrufer blockieren, exakt dieselbe Erwägung wie bei
  `notification-service`/`ocr-service`/`rendering-service`.
- **Warum ein In-Memory-Cache statt eines neuen persistierten Payload-Felds**: die Alternative (Payload
  doch in der DB ablegen, nur für die Dauer des Retry-Fensters) würde das in ADR 0028 explizit
  begründete Privacy-/Audit-Prinzip unterlaufen — genau der Datensatz, den der Hub laut Konzept NIE sehen
  soll, läge dann (wenn auch temporär) in dessen Datenbank. Ein Neustart-Verlust ist die ehrliche,
  dokumentierte Konsequenz aus diesem Prinzip, kein Implementierungsversehen.
- **Warum die manuelle Retry-Rückmeldung bei fehlendem Cache-Eintrag `409` statt eines stillen No-Ops
  liefert**: der Aufrufer (ein Hub-Betreiber oder eine Admin-UI) muss erfahren, dass eine automatische
  Nachstellung hier NICHT möglich ist, damit er die Absenderinstallation zu einem neuen Handover mit
  neuer `handover_id` anstoßen kann — `repository.create_handover` legt Zeilen ohne Existenzprüfung an
  (bloßes `session.add`), ein Retry mit derselben `handover_id` nach Cache-Verlust ist daher ohnehin
  keine Option (`IntegrityError` auf dem Primärschlüssel).
- **Warum `"delivery_failed"` als Name des terminalen Zustands beibehalten wird** (Abweichung von
  ADR 0078–0080s `..._permanent`-Konvention): explizite Plan-Vorgabe für diese Session — der neue
  Zwischenstatus `"pending_retry"` ist der eigentliche neue Vokabelbestandteil, `"delivery_failed"`
  existierte bereits als Statuswert und wird nur zeitlich später erreicht (erst nach Erschöpfung statt
  sofort).
- **Warum `POST /handovers/{id}/result` (die "Ergebnis"-Rückleitung) NICHT Teil dieser Session ist**:
  strukturell symmetrisch, aber architektonisch eigenständig (andere Richtung, andere Installation ruft
  zurück) — der Plan grenzt den Umfang dieser Session explizit auf die Erstzustellung ein; eine gleiche
  Behandlung der Ergebnis-Rückleitung wäre eine sinnvolle Folgesession, kein Bestandteil dieser.

## Konsequenzen

- **Migration bereits laufender Installationen**: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` im Lifespan
  für `attempts`/`next_retry_at` auf `federation.handover`.
- **Neue, dokumentierte Betriebsgrenze**: ein Neustart des Hub während eines offenen Retry-Fensters
  (`pending_retry`) verliert den zwischengespeicherten Payload unwiederbringlich — sowohl der
  automatische Poll-Tick als auch ein manueller `POST .../retry` markieren den betroffenen Handover dann
  als `delivery_failed`, statt endlos auf einen nie eintreffenden Payload zu warten bzw. mit `409` zu
  antworten. Dokumentiert in `models.Handover`s Docstring, im Lifespan-Kommentar und in
  `docs/services/federation-hub-service.md` "Offene Punkte" (ersetzt die bisherige, jetzt erledigte
  "kein Retry"-Zeile durch diese präzisere, verbleibende Einschränkung).
- **Echter Design-Bug beim Selbstentwurf gefunden und behoben, VOR der Live-Verifikation**: die
  ursprüngliche Fassung entfernte den Cache-Eintrag bei JEDEM Übergang nach `delivery_failed`
  (Erschöpfung), nicht nur bei Erfolg — genau der Moment, in dem `POST .../retry` erstmals erlaubt ist
  (dessen 409-Gate verlangt `status == "delivery_failed"`). Der Cache wäre damit in der Praxis fast immer
  bereits leer gewesen, wenn ein Admin den Retry-Endpunkt tatsächlich aufrufen durfte — der "manuelle
  Neustart"-Pfad hätte fast nie funktioniert, ohne dass ein Test dies aufgedeckt hätte (die ursprünglichen
  Tests injizierten den Cache-Eintrag manuell, statt das reale Verhalten zu prüfen). Behoben: der
  Cache-Eintrag wird jetzt ausschließlich bei tatsächlich ERFOLGREICHER Zustellung entfernt (in
  `create_handover`, `retry_handover` und `_run_retry_tick` einheitlich), bleibt also auch nach
  Erschöpfung erhalten, bis entweder ein Retry erfolgreich ist oder der Hub neu startet. Ähnliche
  Fundkategorie wie der `reset_for_retry`-Bug in ADR 0080, hier aber beim eigenen Entwurf vor der
  Live-Verifikation bemerkt statt erst danach.
- **Tests**: 43 (vorher 35, +8: `pending_retry`-Verhalten bei unerreichbarem Ziel statt sofortigem
  `delivery_failed`, Erschöpfung nach `delivery_failed` MIT weiterhin zwischengespeichertem Payload,
  Regressionstest für den oben beschriebenen Cache-Bug über den Poll-Loop, `/retry`-Endpunkt-Statusgate,
  erfolgreicher manueller Retry, `409` bei per Simulation verlorenem Cache-Eintrag, neue `test_main.py`
  mit vier `_run_retry_tick`-Tests inkl. Erschöpfung und Cache-Verlust). Neue `session_factory`-Fixture in
  `conftest.py` (fehlte bislang, gleiche Lücke wie bei den drei vorherigen Services dieser Phase).
- **Live gegen den echten laufenden Stack verifiziert** (Image-Neubau + Neustart von
  `federation-hub-service`, Migration bestätigt, **zweimal** wegen des oben beschriebenen
  Cache-Bugfixes): `POST /handovers` gegen eine absichtlich unerreichbare `callback_base_url` liefert
  `pending_retry` mit `attempts=1`; der reale, laufende `_handover_retry_poll_loop` (60s-Intervall)
  greift den fälligen Handover eigenständig auf und erhöht `attempts` bei jedem Tick — über **echte
  ~5 Minuten Wartezeit** (kein beschleunigtes Setting) bis zur tatsächlichen Erschöpfung verfolgt:
  `attempts` steigt 1→2→3→4→5, `status` wechselt exakt bei Erreichen von `max_handover_delivery_attempts`
  von `pending_retry` auf `delivery_failed`; der Payload-Cache-Eintrag bleibt dabei nachweislich erhalten
  (nicht der ursprüngliche Bug); `POST .../retry` gegen das weiterhin unerreichbare Ziel bestätigt live
  den `reset_for_retry`-Pfad (`attempts` startet wieder bei 0, landet nach dem einen synchronen Versuch
  bei 1, NICHT bei 6 — derselbe Bugtyp wie in ADR 0080, hier von vornherein korrekt). Die
  Neustart-Grenze wurde mit einem ECHTEN `docker compose restart federation-hub-service` verifiziert
  (kein simuliertes Leeren): zwei zu diesem Zeitpunkt noch `pending_retry`-Handover wurden vom
  Log bestätigt mit `federation_handover_retry_payload_lost` markiert und landeten korrekt bei
  `delivery_failed`; ein anschließender `POST .../retry` auf einen davon lieferte `409` mit der
  dokumentierten Meldung, dass die Absenderinstallation einen neuen Handover einreichen muss.
