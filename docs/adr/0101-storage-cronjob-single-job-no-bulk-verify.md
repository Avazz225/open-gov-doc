# 0101 — Storage-CronJob: nur EIN CronJob (Replikation), kein zweiter für Fixity-Verifikation

**Status:** akzeptiert (P26-S4, siehe `IMPLEMENTATION_PLAN.md`)
**Kontext:** Konzept 3.6, betrifft `infra/k8s/dms/` (Phase 26, Fortsetzung von [ADR 0099](0099-helm-single-chart-values-driven-service-map.md)/[ADR 0100](0100-helm-secrets-existing-secret-pattern.md)), löst den in [ADR 0004](0004-storage-redundancy-scope.md) und PROGRESS.md P20-S6 angekündigten externen Träger für `storage-service`s On-Demand-Endpunkte ein.

## Entscheidung

Der neue `templates/storage-cronjob.yaml` rendert **genau einen** `CronJob`
(`storageCronJob.enabled`), der periodisch `POST /replication/process-pending`
gegen `storage-service`s In-Cluster-Service-DNS aufruft
(`http://<fullname>-storage-service:8000`, via neuem `dms.storageServiceBaseUrl`-
Helfer, gleiche URL-Formel wie `dms.dependsOnServicesEnv`). Es gibt bewusst
**keinen** zweiten CronJob für die im Session-Briefing angenommene periodische
Objekt-Fixity-Verifikation.

## Begründung

**Der reale Verifikations-Endpunkt ist kein Bulk-Endpunkt.** Das
Session-Briefing ging (mit ausdrücklichem Verifikationsauftrag) von einem
Endpunkt "etwa `/object-verify/.../all`" aus, der periodisch "alle Objekte"
prüfen könnte. Der tatsächliche Code
(`services/storage-service/src/storage_service/main.py` Zeile 523-542,
`@app.get("/object-verify/{key:path}/all")`) verifiziert stattdessen **alle
konfigurierten Ziele/Kopien EINES per Pfad-Parameter übergebenen einzelnen
Objekt-`key`** — nicht alle im Store vorhandenen Objekte. Das deckt sich mit
der eigenen Doku
(`docs/services/storage-service.md`: "Fixity-Check über **alle** konfigurierten
Ziele" — "Ziele" bezieht sich auf die Redundanz-Ziele eines Objekts, nicht auf
die Objektmenge) sowie mit dem "Offene Punkte"-Abschnitt derselben Datei, der
"Keine automatische periodische Ausführung von `/object-verify/.../all`" als
bekannte Lücke nennt, ohne einen Enumerationsmechanismus zu versprechen.

`storage-service` hat aktuell **keinen** Endpunkt, der Objektschlüssel
auflistet oder eine Charge noch nicht (oder am längsten nicht) verifizierter
Objekte zurückgibt — anders als bei der Replikations-Retry-Queue
(`repository.list_pending_copies`/`POST /replication/process-pending`, seit
ADR 0082 mit Full-Jitter-Backoff über `ObjectCopy.next_retry_at`) existiert
für Fixity kein Pendant zu `next_retry_at`. `ObjectMetadata` (models.py) trägt
kein `last_verified_at`/`next_verify_at`-Feld, `repository.py` hat keine
`list_unverified`-artige Abfrage.

Ein CronJob kann von außen nur aufrufen, was die API tatsächlich anbietet.
Einen periodischen "verifiziere alles"-Job gegen einen Endpunkt zu bauen, der
zwingend einen konkreten, im Voraus bekannten `key` verlangt, wäre entweder
funktionslos (fester Platzhalter-Key, verifiziert für immer nur ein einziges
Objekt) oder würde eine Objekt-Enumeration von außen erfordern (z. B. über
einen anderen Service mit Kenntnis aller Storage-Keys, mit Paginierung/
Fehlerbehandlung in einem reinen curl-Shell-Skript) — beides verwässert die
eigentliche fachliche Anforderung (regelmäßiger Fixity-Check **aller**
Kopien, Konzept 3.6) auf eine Weise, die suggerieren würde, es funktioniere
vollständig, obwohl es das nicht täte. Einen neuen Bulk-Endpunkt direkt in
`storage-service` zu ergänzen wäre die saubere Lösung, sprengt aber den Scope
dieser Helm-Chart-Session (P26-S4 laut `IMPLEMENTATION_PLAN.md` betrifft
`infra/k8s/dms/`, nicht Service-Code) und wäre ohne eigene Tests/ADR/
Doku-Update für `storage-service` selbst unvollständig.

**Auth**: Weder `/replication/process-pending` noch
`/object-verify/{key}/all` verlangen aktuell einen Auth-Header (kein
`Header(...)`/`Depends(...)`-Gate in `main.py`, anders als z. B.
`DELETE /objects/{key}`s optionaler `X-DMS-Roles`-Governance-Bypass). Der
CronJob sendet trotzdem `X-DMS-Principal: system:storage-replication-cronjob`
mit (gleiches Muster wie `archival-service`s `_SYSTEM_PRINCIPAL_HEADERS`/
`workflow-service`s `X-DMS-Principal`-Prüfung für andere Service-zu-Service-
Aufrufe) — kostet nichts, hält den Aufruf im projektweiten Muster konsistent
und macht ihn in Logs als Maschinenaufruf erkennbar, auch falls
`storage-service` diesen Endpunkt später gated.

**Utility-Image**: `curlimages/curl:8.10.1` statt eines vollen
`storage-service`-Images — der Container macht nichts weiter als einen
einzelnen HTTP-POST. Keine bestehende Projekt-Konvention für ein
Utility-Image gefunden (`infra/docker-compose.yml` nutzt curl/wget nur
innerhalb der jeweiligen Service-Images für Healthchecks), daher das
gängige offizielle Minimal-Image gewählt.

## Konsequenzen

- Sekundärkopien werden ab dieser Session tatsächlich automatisch
  nachgezogen (`POST /replication/process-pending` alle 15 Minuten, Default
  `storageCronJob.replication.schedule`) — der in ADR 0004/PROGRESS.md P20-S6
  offen gelassene "externe Träger" ist damit real vorhanden.
- Regelmäßige Fixity-Verifikation **aller** Objekte bleibt weiterhin ein
  manueller/On-Demand-Vorgang (`GET /object-verify/{key}/all` je Objekt) —
  Konzept 3.6s "regelmäßiger Fixity-Check über alle Kopien" ist damit für
  Phase 26 NICHT vollständig erfüllt. `values.yaml`s
  `storageCronJob.verification.enabled: false` ist ein bewusst unverdrahteter
  Platzhalter (kein Template liest ihn) für eine spätere Session.
- **Empfohlener Zuschnitt einer künftigen Lösung** (nicht Teil dieser
  Session): `storage-service` bekommt, analog zur Replikations-Retry-Queue,
  ein neues Feld `ObjectMetadata.next_verify_at` (oder eine eigene
  `verification_schedule`-Tabelle) plus einen neuen Endpunkt
  `POST /object-verify/process-pending?limit=N`, der die `N` am längsten
  unverifizierten Objekte auswählt, `verify_all_copies` je Objekt aufruft und
  `next_verify_at` mit einem festen Intervall (kein Retry-Backoff nötig, da
  kein Fehlerfall im ADR-0082-Sinne) neu setzt — spiegelbildlich zu
  `list_pending_copies`/`process_pending`. Erst dann lässt sich ein zweiter,
  echter CronJob analog zu diesem hier bauen.
- Sollte `storage-service` diese beiden Endpunkte später mit einem echten
  Auth-Gate versehen (`X-DMS-Principal`/`X-DMS-Roles`-Pflicht), funktioniert
  dieser CronJob unverändert weiter, da der Principal-Header bereits
  mitgeschickt wird.
