# 0030 — Storage-WORM: Anwendungsschicht-Guard + S3 Object Lock im Governance-Mode

**Status:** akzeptiert
**Kontext:** Konzept 5.1/5.2a ("Aufbewahrungsfristen"/"Zwangslöschung"), Session P7-S1 — Aufbewahrung/Legal Hold/Zwangslöschung inkl. Löschregister

## Entscheidung

`storage-service` bekommt einen zweistufigen WORM-Schutz statt eines einzigen, backend-spezifischen Mechanismus:

1. **Anwendungsschicht-Guard** (`retention_guard.py`, gleiches Muster wie [ADR 0017](0017-storage-device-identity-guard.md)s `identity_guard.py`): jede `ObjectCopy` bekommt ein optionales `retention_until`-Datum, unabhängig vom Backend-Typ. `DELETE /objects/{key}` prüft vor jeder Löschung, ob ein Ziel mit `object_lock_mode="governance"` eine Kopie mit `retention_until` in der Zukunft hat — falls ja, ist die Löschung nur mit `?bypass_governance=true` **und** einer Rolle aus `Settings.governance_bypass_role` (Default `dms-admin`, geprüft über den vom Gateway injizierten `X-DMS-Roles`-Header) möglich, sonst `403`.
2. **Echtes S3 Object Lock als zusätzliche Härtung** für `type="s3"`-Ziele mit gesetztem `object_lock_mode`: `write()` setzt `ObjectLockMode="GOVERNANCE"`/`ObjectLockRetainUntilDate`, `delete()` nutzt `BypassGovernanceRetention=True` beim autorisierten Bypass. Für `type="local"`-Ziele bleibt es bei der reinen Anwendungsschicht-Prüfung — ehrlich dokumentierte Grenze, kein vorgetäuschter Schutz, wo es technisch keine Entsprechung gibt.

**Bewusst nur `"governance"` als gültiger Wert für `object_lock_mode`** (kein `"compliance"` im Schema). Compliance-Mode würde die vom Konzept selbst verlangte, sanktionierte Zwangslöschungs-Ausnahme (5.2a) technisch unmöglich machen — selbst ein AWS-Root-Account kann eine Compliance-Mode-Sperre vor Ablauf nicht aufheben.

**Keine automatische Bucket-Migration**: `ObjectLockEnabledForBucket=True` wird nur im `create_bucket`-Zweig von `ensure_bucket()` gesetzt (neu angelegter Bucket). Für den bereits bestehenden, produktiv genutzten Dev-Bucket bleibt der `head_bucket`-Erfolgszweig ein reines No-Op — S3 Object Lock lässt sich nicht nachträglich auf einen bestehenden Bucket aktivieren, ein automatischer Eingriff (Neuanlage + Datenumzug) wäre riskant und lag außerhalb des Sessionsumfangs. Verifikation erfolgt über ein zusätzliches, rein testweises Zweit-Ziel mit frischem Bucket (gleiches Vorgehen wie in P5c-S2).

## Begründung

- **Portabilität vor Vollständigkeit**: Ein reiner S3-Object-Lock-Ansatz hätte für `local`-Backend-Ziele (NFS/PVC-Mount) keine Entsprechung — die Anwendungsschicht-Prüfung funktioniert dagegen unabhängig vom Backend-Typ und ist die eigentliche Durchsetzungsinstanz; S3 Object Lock ist eine zusätzliche, nicht die alleinige Absicherung.
- **`retention_until` auf jeder `ObjectCopy`, nicht nur auf gelockten Zielen**: vereinfacht `record_copy`/`replication.py` (ein Feld, immer geschrieben, unabhängig davon ob das jeweilige Ziel `object_lock_mode` gesetzt hat) und hält die Tür offen, ein Ziel nachträglich auf Governance-Mode umzustellen, ohne bestehende Kopien nachpflegen zu müssen.
- **Rollenprüfung exakt wie `document-service`s `kennzeichen_admin_role`** (P5e-S2): kein neuer Autorisierungsmechanismus, sondern Wiederverwendung des bereits etablierten `X-DMS-Roles`-Header-Musters.
- **Governance- statt Compliance-Mode ist keine Verwässerung des Aufbewahrungsschutzes**: 5.2 verlangt eine reguläre, durchgesetzte Frist — 5.2a verlangt *ausdrücklich* eine sanktionierte Ausnahme davon (Zwangslöschung mit Vier-Augen-Prinzip). Governance-Mode mit rollengebundenem Bypass bildet exakt diese Kombination ab; Compliance-Mode würde die zweite Anforderung technisch ausschließen.

## Konsequenzen

- `StorageBackend.write()`/`delete()` sind jetzt keine minimalen Interfaces mehr (`lock_until`/`bypass_governance` als neue Keyword-Argumente) — ein bewusster, im Rahmen dieser Session in Kauf genommener Bruch, da beide Parameter für WORM unverzichtbar sind und alle drei Implementierungen (`local_backend.py`, `s3_backend.py`, Testdoubles) im selben Commit mitaktualisiert wurden.
- `document-service` ist der einzige aktuelle Aufrufer, der `retain_until` beim Schreiben mitgibt (aus `Document.retention_until`, seedbar über `ObjectType.default_retention_days`) — andere Services, die `storage-service` nutzen, sind von dieser Erweiterung unberührt (Parameter ist optional, Default `None`).
- Der `local`-Backend-Typ bietet weiterhin **kein** echtes WORM — nur die Anwendungsschicht-Prüfung. Wer echten Manipulationsschutz auf lokalem Storage braucht, muss ein S3-kompatibles Ziel mit `object_lock_mode=governance` einsetzen. Dokumentiert als bewusste, keine versehentliche Lücke.
- `replication.py`s `process_pending` propagiert `retention_until` an `record_copy`, aber (noch) nicht `lock_until` an den eigentlichen Backend-`write()`-Aufruf bei nachgeholter Replikation — eine bestehende Lücke, die erst relevant wird, sobald Nachreplikation regelmäßig für Governance-Ziele genutzt wird (Retry-Queue-Fall, siehe ADR 0017).
