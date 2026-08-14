# 0084 — fleet-management-service & license-service: Schlüsselrotation

**Status:** akzeptiert (Session 1 von 4, siehe Phase 21 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 21 Session 1, betrifft `fleet-management-service` und `license-service`

## Entscheidung

Der Plan nennt als Vorbild `workflow-service`s bereits existierendes `POST /federation/rotate-key`
(ADR 0039): "neuer Schlüssel wird ausgestellt, alter bleibt kurz gültig für einen Übergang, dann
invalidiert". Eine genauere Prüfung dieses Vorbilds ergab: es gibt dort **kein zeitbasiertes
Übergangsfenster** — `federation-hub-service.repository.rotate_installation_key` ersetzt
`Installation.public_key_pem` sofort und atomar, sobald die Rotationsanfrage (signiert mit dem noch
aktuellen Schlüssel) erfolgreich verifiziert wurde. "Alter bleibt kurz gültig" bedeutet dort: der alte
Schlüssel bleibt der einzig gültige, bis der Rotationsaufruf durchläuft, danach ist ausschließlich der
neue gültig — kein Zeitraum, in dem beide gleichzeitig akzeptiert würden. Diese Session repliziert dieses
Muster für `fleet-management-service`, wo es strukturell genauso passt, und entwickelt für
`license-service` eine abweichende, aber sinngemäß passende Variante, da dort technisch gar kein
selbst erzeugter Signierschlüssel existiert, der "rotiert" werden könnte.

### fleet-management-service

Neuer Endpunkt `POST /installations/{id}/rotate-key` — ersetzt `ManagedInstallation.fleet_agent_api_key`
sofort und atomar (`repository.rotate_managed_installation_key`), optional mit einem vom Betreiber
mitgegebenen Wert (gleiche Flexibilität wie der bestehende `POST /installations`), sonst wird ein neuer
Wert erzeugt. Response-Schema identisch zur Erstanlage (`ManagedInstallationCreateOut`) — der
Klartext-Schlüssel wird nur in dieser einen Antwort zurückgegeben.

### license-service

`fleet_agent_api_key` bei `fleet-management-service` ist ein selbst erzeugter, selbst verwalteter
Schlüssel — `license-service`s "Signierschlüssel" ist etwas fundamental anderes: der private Schlüssel,
mit dem Lizenzdateien signiert werden, gehört dem **Lizenzgeber** und liegt laut ausdrücklicher
ADR-0032-Vorgabe **nie** in diesem Repository/Deployment ("nirgends im Repository ... darf auftauchen").
`license-service` besitzt nur den öffentlichen **Verifikationsschlüssel**
(`settings.license_public_key_pem`) — es gibt hier keinen selbst erzeugten Schlüssel, den dieser Service
rotieren könnte. Eine 1:1-Übertragung von `fleet-management-service`s Muster ist daher nicht anwendbar.

Stattdessen: neue, optionale Einstellung `license_previous_public_key_pem` (Default `None`). Wechselt der
Lizenzgeber sein Schlüsselpaar, konfiguriert der Betreiber `license_public_key_pem` auf den NEUEN
öffentlichen Schlüssel und `license_previous_public_key_pem` auf den ALTEN. `license_verifier.decode()`
versucht `public_key_pem` zuerst, bei dessen Fehlschlag den optionalen `previous_public_key_pem` —
**hier gibt es tatsächlich ein echtes Übergangsfenster** (anders als bei den beiden anderen
Rotationsmustern in diesem Projekt, ADR 0039/fleet-management-service oben): bereits installierte, unter
dem alten Schlüssel signierte Lizenzen bleiben bei jeder erneuten Statusprüfung (`GET /license/status`,
liest `raw_token` erneut) gültig, während neu ausgestellte Lizenzen bereits mit dem neuen Schlüssel
signiert sein können. Der Betreiber setzt `license_previous_public_key_pem` nach Abschluss der
Übergangsfrist wieder auf `None` zurück ("dann invalidiert").

## Begründung

- **Warum bei `fleet-management-service` KEIN Übergangsfenster, obwohl `workflow-service`s Vorbild
  eines hat**: strukturell unterscheidet sich die Vertrauensrichtung. Bei der Hub-Installations-Rotation
  bestätigt die ZIELSEITE (der Hub) die Rotation aktiv per HTTP-Aufruf und kann daher exakt in dem
  Moment umschalten, in dem sie den neuen Schlüssel entgegennimmt. Bei `fleet_agent_api_key` gibt es
  keine analoge Rückkopplung: `fleet-management-service` PRÄSENTIERT den Schlüssel nur, die
  Zielinstallation verifiziert ihn gegen einen **statisch beim eigenen Start aus einer Env-Var**
  gelesenen Wert (`DMS_FLEET_AGENT_API_KEY`) — es gibt keinen Kanal, über den `fleet-management-service`
  diesen Wert auf der Zielinstallation zur Laufzeit ändern könnte. Ein "Übergangsfenster" ließe sich hier
  technisch gar nicht abbilden; die Rotation bleibt unvermeidbar ein zweischrittiger, teilweise manueller
  Vorgang (siehe "Konsequenzen").
- **Warum `license-service`s Lösung NICHT als "Schlüsselrotation" im wörtlichen Sinne umgesetzt wird**:
  es gibt keinen Schlüssel dieses Service, der rotiert werden könnte — nur einen extern ausgestellten
  Vertrauensanker, der ausgetauscht wird. Die Lösung überträgt die Grundidee ("alter Zustand bleibt kurz
  gültig, dann invalidiert") auf das, was hier tatsächlich existiert: den öffentlichen
  Verifikationsschlüssel.
- **Warum kein `models.py`/keine DB-Tabelle für `license-service`s vorherigen Schlüssel**: der Service
  hat für seine Kernaufgabe (Lizenzverifikation) ohnehin nur Settings, keinen Signierschlüssel-Datensatz
  (anders als `federation-hub-service`s `HubIdentity`/`signature-service`s `InternalCa`) — ein einzelner
  optionaler Konfigurationswert genügt, keine neue Persistenzebene nötig.

## Konsequenzen

- **`fleet-management-service`: Rotation bleibt ein zweischrittiger, teilweise manueller Vorgang** —
  dokumentiert im Endpunkt-Docstring und in `docs/services/fleet-management-service.md`: nach
  `POST .../rotate-key` ist der neue Wert nur auf der `fleet-management-service`-Seite aktiv; bis ein
  Betreiber die Zielinstallation manuell auf `DMS_FLEET_AGENT_API_KEY=<neuer Wert>` umstellt und
  neu startet, schlagen ausgehende Aufrufe (`GET .../status`, `POST .../license`, `POST .../provision`)
  mit `401`/`403` fehl — ein bewusst in Kauf genommener, ehrlich dokumentierter Zustand statt eines
  scheinbar automatischen, tatsächlich aber nicht funktionierenden Rundum-Mechanismus. Der optionale
  `fleet_agent_api_key`-Parameter im Request erlaubt die umgekehrte, empfohlene Reihenfolge (Installation
  zuerst umstellen, dann hier nachziehen), die diese Fehlerlücke praktisch vermeidet.
- **Migration**: keine nötig — beide Änderungen sind additiv (neuer Endpunkt bzw. neue optionale
  Einstellung mit rückwärtskompatiblem Default `None`).
- **Tests**: `fleet-management-service` 30 (vorher 26, +4: Standardgenerierung, Betreiber-vorgegebener
  Wert, tatsächliche Verwendung des neuen Werts bei einem ausgehenden Aufruf, `404` bei unbekannter
  Installation). `license-service` 37 (vorher 32, +5, alle in `test_license_verifier.py`: Fallback auf
  den vorherigen Schlüssel während der Übergangsfrist, Bevorzugung des aktuellen Schlüssels ohne
  Fallback-Notwendigkeit, Fehlschlag wenn weder aktueller noch vorheriger Schlüssel passt,
  unverändertes Verhalten ohne konfigurierten vorherigen Schlüssel).
- Doku: `docs/services/fleet-management-service.md`/`license-service.md` ("Offene Punkte" als behoben
  markiert, neue Endpunkt-/Einstellungsdokumentation).
- **Live gegen den echten laufenden Stack verifiziert** (Image-Neubau + Neustart beider Services):
  `fleet-management-service` — eine echte Installation registriert, `POST .../rotate-key` sowohl mit
  automatisch erzeugtem als auch mit betreiber-vorgegebenem Wert bestätigt (jeweils tatsächlich
  geänderter Rückgabewert), `404` für eine unbekannte Installation bestätigt. `license-service` — über
  eine temporäre Compose-Override-Datei (`DMS_LICENSE_PUBLIC_KEY_PEM` auf einen frisch erzeugten,
  unabhängigen Schlüssel gesetzt, `DMS_LICENSE_PREVIOUS_PUBLIC_KEY_PEM` auf den bisherigen
  Standardschlüssel) real nachgestellt: eine bereits vor dieser Session unter dem alten Schlüssel
  installierte, echte Lizenz blieb über `GET /license/status` gültig (Fallback-Pfad tatsächlich
  durchlaufen, kein Mocking); als Gegenprobe ohne konfigurierten vorherigen Schlüssel wurde dieselbe
  Lizenz korrekt als `valid=false`/`"Lizenzsignatur ungueltig"` gemeldet — bestätigt, dass der Fallback
  echte Verifikationsarbeit leistet statt eine Lücke zu öffnen. Nach dem Test auf die ursprüngliche
  Konfiguration zurückgesetzt (erneut `valid=true` bestätigt).
