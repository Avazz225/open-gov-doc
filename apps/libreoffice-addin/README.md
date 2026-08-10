# libreoffice-addin

LibreOffice/OpenOffice-**Writer**-Erweiterung (UNO-API, Python, `.oxt`) für
native OG-Doc-Integration (Konzept 3.3a), P14-S9 — das Gegenstück zum
Microsoft-Office-Add-in (`apps/office-addin`, P14-S8). Öffnen/Speichern,
inline Metadatenbearbeitung, Workflow-Start/-Fortsetzung, zentrale
Vorlagenbibliothek. Spricht ausschließlich bereits bestehende
`document-service`/`workflow-service`/`object-type-service`/`folder-service`/
`search-service`-Endpunkte an, kein neuer Backend-Code (siehe
[ADR 0046](../../docs/adr/0046-libreoffice-addin-writer-only-dialog-hub-loadcomponent.md)).

Ausführliche Doku: [`docs/services/libreoffice-addin.md`](../../docs/services/libreoffice-addin.md).

## Build

```bash
python3 build.py
# erzeugt OgDocAddin.oxt im selben Verzeichnis
```

Reine Standardbibliothek (`zipfile`) - kein Node/npm-Tooling nötig.

## Installation

```bash
unopkg add OgDocAddin.oxt          # pro Nutzer (kein Root nötig)
unopkg add --shared OgDocAddin.oxt # systemweit (braucht Schreibrechte)
unopkg list --verbose              # Installation prüfen
unopkg remove org.ogdoc.writer-addin
```

Nach der Installation erscheint "Extras > OG Doc öffnen..." in jedem
geöffneten Writer-Textdokument.

## Tests

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```

Reines `unittest`, keine Drittanbieter-Testabhängigkeit. `tests/uno_mock.py`
stellt einen handgeschriebenen Fake der UNO-Laufzeitumgebung bereit (kein
echter LibreOffice-UNO-Skript-Bridge-Zugang in der Referenz-
Entwicklungsumgebung möglich, siehe ADR 0046 "Verifikation").

## Lokale Entwicklung / manuelles Testen mit echtem LibreOffice

Falls eine lokale LibreOffice-Installation vorhanden ist:

```bash
python3 build.py
unopkg add OgDocAddin.oxt
soffice --writer  # neues Writer-Dokument öffnen, dann Extras > OG Doc öffnen...
```

Erwartet ein laufendes Gateway auf `http://localhost:8009`:

```bash
cd ../../infra && docker compose up -d
```

Die Gateway-Adresse ist im Anmelde-Dialog der Erweiterung selbst editierbar
(kein Build-Zeit-Einbrennen wie bei den Next.js-Apps nötig, da dieses Paket
keinen eigenen Build-Schritt für Frontend-Konfiguration hat).
