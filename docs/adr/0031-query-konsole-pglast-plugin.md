# 0031 — Diagnose-Query-Konsole: pglast als optionales Plugin statt gebündelter GPL-3.0-Abhängigkeit

**Status:** akzeptiert
**Kontext:** P8-S0 (Phasen-Kickoff-Planung Phase 8, Konzept 6.1 "Zentrale Query- & Trace-Konsole"/6.2 "CLI-Tool"), `IMPLEMENTATION_PLAN.md` benannte für diese Session explizit die Prüfung des `pglast`/`libpg_query`-Bibliotheksstands und der Lizenz vor P8-S1. Reine Recherche-/Rückfrage-Session, kein Implementierungscode.

## Entscheidung

Konzeptabschnitt 1a/6.1 nennt `pglast` als "entschiedene" Bibliothek für die Query-Konsolen-Sprachimplementierung (Python-Anbindung an den echten PostgreSQL-Parser über `libpg_query`, inkl. AST-Visitor-/Printer-Infrastruktur zur Erweiterung um DMS-spezifische Trace-/Hierarchie-Operatoren). Die tatsächliche Prüfung bei Sessionstart ergab: **`pglast` ist GPL-3.0-or-later lizenziert** (bestätigt über PyPI-Metadaten, aktuelle Version 8.4 vom 2026-07-22, aktiv gepflegt, PostgreSQL-18-Support in der neuesten Branch, PG16 — die hier eingesetzte Version, siehe `infra/docker-compose.yml` — über den älteren `v6`-Branch abgedeckt). `libpg_query` selbst (die zugrunde liegende C-Bibliothek) ist dagegen **BSD-3-Clause**/PostgreSQL License, also unproblematisch — der Lizenzkonflikt entsteht ausschließlich durch `pglast`s eigene Lizenzwahl für den Python-Wrapper.

Geprüfte Alternativen für die Python-Anbindung: **`pgparse`** (BSD-3-Clause, auf PyPI, aber nur 74 Commits/2 Stars — sehr kleines, unreifes Projekt, bietet laut eigener Dokumentation nur Parsen/Normalisieren/Fingerprinting, keine AST-Manipulation oder Prettifier-Infrastruktur) und **`psqlparse`** (BSD, aber faktisch aufgegeben — letzter Stable-Release 2016, letzte Pre-Release 2019). Keine der beiden ist ein vollwertiger Ersatz für `pglast`s Visitor-/Printer-Fähigkeiten, die 6.1 für die Grammatik-Erweiterung explizit braucht.

**Per `AskUserQuestion` bei Sessionstart entschieden**: Der künftige Query & Trace Service (P8-S1) bekommt **dieselbe Plugin-Architektur wie KDBX in [ADR 0029](0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md)** — der Kern definiert eine schlanke Parser-Erweiterungs-Schnittstelle, `pglast` selbst wird **nicht** standardmäßig ins Docker-Image gebündelt, sondern als optionales, vom Betreiber selbst nachzuinstallierendes Plugin dokumentiert und ausgeliefert.

## Begründung

- **Dieselbe GPL-3.0-Argumentation wie ADR 0029**: Unveränderte Nutzung als Dependency wird von der GPL-3.0-Copyleft-Pflicht nicht ausgenommen (anders als LGPL, siehe ADR 0018) — ein gebündeltes `pglast` im self-contained Docker-Image würde den Query & Trace Service (und je nach Auslegung weiterverbreitete Gesamt-Images) der GPL unterstellen.
- **Die geprüften Alternativen sind keine echte Option**: `pgparse` ist zu unreif (74 Commits, 2 Sterne, keine erkennbare breite Nutzung) und bietet nur Parsen/Normalisieren ohne AST-Manipulation/Prettifier — genau die Fähigkeit, die 6.1 für die DMS-spezifischen Zusatzkonstrukte (Trace-/Hierarchie-Operatoren) über `pglast`s Visitor-/Printer-Muster explizit vorsieht. `psqlparse` ist faktisch tot.
- **Plugin-Ansatz statt Bibliothekswechsel oder Eigenimplementierung**: Der Plugin-Ansatz erhält die volle, im Konzept tatsächlich "entschiedene" `pglast`-Funktionalität, statt sie durch eine funktional schwächere Bibliothek zu ersetzen oder einen eigenen SQL-Parser zu schreiben — Letzteres schließt 6.1 selbst explizit aus ("bewusst kein eigener Parser von Grund auf geschrieben").
- **Konsistent mit dem bereits etablierten Plugin-Prinzip** für Storage-Backends (ADR 0017) und KDBX (ADR 0029): Betreiber, die die volle psql-artige Manipulationsfunktion der Query-Konsole nutzen wollen, installieren `pglast` bewusst selbst in ihrer eigenen Umgebung/ihrem eigenen Image-Build — genau der Fall, den GPL-3.0 uneingeschränkt erlaubt, da die Copyleft-Pflicht dann nur den eigenen, nicht weiterverbreiteten Betrieb betrifft.
- **Diese Einschätzung ist keine Rechtsberatung** (gleicher Vorbehalt wie ADR 0018/0029): pragmatische, technische Bewertung für die aktuelle Entwicklungsphase. Vor einem etwaigen Fremdvertrieb des Gesamtsystems ist die Lizenzfrage erneut zu prüfen.

## Konsequenzen

- P8-S1 (Query & Trace Service) entwirft eine Plugin-Schnittstelle für die Parser-/Grammatik-Erweiterung, statt `pglast` direkt zu importieren; das Standard-Docker-Image des Query & Trace Service bleibt GPL-frei.
- Ohne installiertes `pglast`-Plugin bietet der Service nur eingeschränkte Funktionalität — der genaue Umfang eines sinnvollen Minimalbetriebs ohne das Plugin (z. B. reine strukturierte Read-Modell-Abfragen ohne vollen psql-Dialekt) ist bei P8-S1-Sessionstart festzulegen, kein technisches Hindernis, aber bewusst hier vertagt.
- `libpg_query` selbst bleibt unproblematisch (BSD/PostgreSQL License) und könnte bei Bedarf auch direkt gebunden werden — nur `pglast`s eigener Python-Wrapper ist die Lizenzquelle des Problems.
- Wer die volle Manipulationsfunktion der Query-Konsole (6.1) nutzen will, muss das `pglast`-Plugin in der eigenen Umgebung selbst hinzufügen — entsprechend zu dokumentieren (`docs/services/<query-service>.md`, Betriebsdoku), analog zu KDBX.
- Größere Installationen, die stattdessen einen eigenen Parser oder eine kommerzielle Lösung einsetzen wollen, sind von dieser Lizenzfrage ohnehin nicht betroffen.
