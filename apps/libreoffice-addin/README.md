# libreoffice-addin

LibreOffice/OpenOffice **Writer** extension (UNO API, Python, `.oxt`) for
native OG Doc integration (Concept 3.3a), P14-S9 — the counterpart to
the Microsoft Office add-in (`apps/office-addin`, P14-S8). Open/save,
inline metadata editing, workflow start/continuation, central
template library. Talks exclusively to already existing
`document-service`/`workflow-service`/`object-type-service`/`folder-service`/
`search-service` endpoints, no new backend code (see
[ADR 0046](../../docs/adr/0046-libreoffice-addin-writer-only-dialog-hub-loadcomponent.md)).

Detailed documentation: [`docs/services/libreoffice-addin.md`](../../docs/services/libreoffice-addin.md).

## Build

```bash
python3 build.py
# produces OgDocAddin.oxt in the same directory
```

Pure standard library (`zipfile`) - no Node/npm tooling needed.

## Installation

```bash
unopkg add OgDocAddin.oxt          # per user (no root needed)
unopkg add --shared OgDocAddin.oxt # system-wide (needs write permissions)
unopkg list --verbose              # verify installation
unopkg remove org.ogdoc.writer-addin
```

After installation, "Tools > Open OG Doc..." appears in every
open Writer text document.

## Tests

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```

Pure `unittest`, no third-party test dependency. `tests/uno_mock.py`
provides a hand-written fake of the UNO runtime environment (no
real LibreOffice UNO script bridge access possible in the reference
development environment, see ADR 0046 "Verification").

## Local Development / Manual Testing with Real LibreOffice

If a local LibreOffice installation is available:

```bash
python3 build.py
unopkg add OgDocAddin.oxt
soffice --writer  # open a new Writer document, then Tools > Open OG Doc...
```

Expects a running gateway at `http://localhost:8009`:

```bash
cd ../../infra && docker compose up -d
```

The gateway address is editable in the extension's own login dialog
(no build-time baking-in needed as with the Next.js apps, since this package
has no separate build step for frontend configuration).
