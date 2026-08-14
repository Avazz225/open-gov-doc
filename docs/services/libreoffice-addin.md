# libreoffice-addin

**Responsibility:** LibreOffice/OpenOffice **Writer** extension (UNO API, Python, `.oxt` package) for native OG Doc integration (3.3a): opening/saving a document directly from/to OG Doc, inline metadata editing, workflow start/continuation, a central role-based template library. P14-S9, an equivalent counterpart to the Microsoft Office add-in (P14-S8) — "shared backend interface with the MS Office add-in" (roadmap wording).

**Concept Reference:** 3.3a, 3.3, 7.1, 2.2, 2.5
**No own Postgres schema** — a pure client extension, no own backend process. Like `apps/office-addin`, it calls **not a single new endpoint**.
**ADR:** [0046 — Writer only, dialog hub instead of sidebar, `loadComponentFromURL` instead of in-place replacement](../adr/0046-libreoffice-addin-writer-only-dialog-hub-loadcomponent.md)

## Location in the Repo

`apps/libreoffice-addin/` — no Next.js/Node, but a `.oxt` extension package (a ZIP with a fixed structure): `META-INF/manifest.xml`, `description.xml` (+ `description/` text files, `registration/icon.png`), `Addons.xcu` (menu registration), `python/` (the actual implementation, pure Python, no third-party packages). `build.py` packages `OgDocAddin.oxt` from these.

## Feature Scope

| Area | Implementation |
|---|---|
| **Open from OG Doc** | Dialog with search field (`search-service`) + result list → load content (`GET /documents/{id}/content`) → `Desktop.loadComponentFromURL()` opens a NEW Writer window with the content → attempt lock (`POST /documents/{id}/lock`) → persist the link in `UserDefinedProperties`. |
| **New from Template** | Dialog lists documents from the root folder "Vorlagen" → load content → `loadComponentFromURL(..., AsTemplate=True)` — LibreOffice's own "new from template" load option. On first save (title dialog), `POST /documents` with `derived_from_document_id`/`derived_from_version_number`. |
| **Save to OG Doc** | `doc.storeToURL(temp_url, FilterName=...)` exports the current editing state to a temporary file → `POST /documents/{id}/versions` (check-in, `expected_base_version_number` like any other client). |
| **Inline Metadata** | Dialog: title + one text field per object-type attribute (`GET /object-types/{id}`) → `PATCH /documents/{id}`. |
| **Start/Continue Workflow** | Dialog: `GET /instances?business_key={documentId}` + `GET /instances/{id}/tasks`, `POST .../complete`, `POST /process-definitions/{id}/instances` with `business_key={documentId}`. |

A single menu entry ("Tools > Open OG Doc...", `Addons.xcu`) opens a "hub" dialog with context-dependent buttons (not signed in → only "Sign in"; signed in without a link → "Open"/"New from Template"; linked → "Metadata"/"Save"/"Workflow"/"Unlink") — the same one-button-opens-everything idea as the ribbon button in `apps/office-addin` (P14-S8), here as a dialog chain instead of a persistent web task pane (UNO has no lightweight sidebar equivalent in Python, see ADR 0046).

## Document Link: `UserDefinedProperties` Instead of Server State

Analogous to Office.js's `document.settings` (ADR 0045): `document.getDocumentProperties().UserDefinedProperties` stores three values directly in the file itself (ODF `meta.xml`/OOXML core properties) — `ogdoc_document_id`, `ogdoc_version_number`, **`ogdoc_content_type`**. The content type is deliberately stored as well (not just ID+version): a bug found before this session's live test would otherwise ALWAYS have converted to ODF on save, regardless of the original format (e.g. DOCX) — see ADR 0046 "Rationale".

## Opening Creates a NEW Window (Not In-Place)

Unlike Word JS's `insertFileFromBase64(..., replace)`, the Writer API has no comparably robust "replace the current document" capability. Instead: downloaded bytes → temporary local file → `Desktop.loadComponentFromURL(url, "_blank", 0, props)` opens it as a genuine new window — more idiomatic for a desktop program, see ADR 0046. Subsequent actions (metadata/save/workflow) act on this newly opened window (`_STATE["working_doc"]` is updated accordingly).

## `dms_client.py`: Pure Standard Library

LibreOffice's bundled Python interpreter has no preinstalled third-party packages (no `requests` without additional intervention) — `urllib.request` is deliberately the only HTTP dependency, no installation step for end users. Mirrors exactly the same gateway calls as `apps/office-addin/src/lib/api.ts` (P14-S8).

## Template Library

Identical to `apps/office-addin` (P14-S8): a template is an ordinary document in the root folder `Vorlagen` (constant `TEMPLATE_LIBRARY_FOLDER_NAME` in `ogdoc_addin.py`) — role-basedness follows automatically from the existing folder read-permission check, no second implementation of this concept.

## Build & Installation

```bash
cd apps/libreoffice-addin
python3 build.py                 # erzeugt OgDocAddin.oxt
unopkg add OgDocAddin.oxt         # pro Nutzer installieren (kein --shared ohne Root-Rechte)
```

The menu "Tools > Open OG Doc..." then appears in every open Writer text document.

## Tests

- `python3 -m unittest discover -s apps/libreoffice-addin/tests`: **30 tests**, pure `unittest`, no third-party test dependency.
  - `test_dms_client.py` (6): multipart construction (fields + file part, `None` values are skipped), `ApiError` translation of HTTP errors including the `detail` field, successful JSON responses, content-type return on download.
  - `test_settings_store.py` (9): session persistence (load/save/delete, isolated temporary file per test) AND **real** document-link logic against a hand-written fake of UNO's `UserDefinedProperties`/`PropertySetInfo` (`tests/uno_mock.py`) — set/read/overwrite/delete.
  - `test_ogdoc_addin_pure.py` (15): pure business logic with no UNO dependency (hub status text per sign-in/link state, which hub buttons appear per state, attribute-field filtering, file-extension detection per content type) — imports the FULL `ogdoc_addin.py` module in the process (with `unohelper`/`com.sun.star.awt` mocked), a genuine wiring/reference-error test.
- **Real `.oxt` installation via `unopkg add`** (see ADR 0046 "Verification") — exit 0, `unopkg list --verbose` confirms registration of both the package AND `Addons.xcu`, all four `python/*.py` files land correctly in the directory expected by `ScriptProviderForPython`. Cleanly uninstalled afterward.
- **No working headless UNO script-bridge access** in this development environment (`soffice --accept=...` fails regardless of transport, see ADR 0046 "Verification" for the diagnosis) — the dialog-construction/action functions themselves are therefore only import-/wiring-checked, not behavior-tested against real `UnoControlDialog` objects.

## Open Points

- **No real click-through/sideload test possible in Writer** in this development environment (see above) — a human should actually install `OgDocAddin.oxt` and click through it before production use.
- **Writer only** — Calc/Impress are entirely untouched (no comparable "load entire document" API with the same robustness).
- **The dialog UI is deliberately functional and plain** (programmatic AWT controls instead of `.xdl` resources, one text field per attribute without type-specific widgets) — an identical, documented simplification to `apps/office-addin`'s `MetadataForm` (P14-S8).
- **The template library requires manual admin setup** (create the folder "Vorlagen", grant read permissions) — no automated bootstrap.
- **No deleting documents/folders, no retention/legal-hold access** from the extension — deliberately limited to the 3.3a feature scope.
- **`ReferenceOOoMajorMinor` version-check pitfall** (see ADR 0046) — for future minimum-version changes, check again against the internal `4.x` compatibility value, not the real product version.
- **No `--shared` system-installation test possible** (root privileges not available in this environment) — only the regular per-user installation path (`unopkg add` without `--shared`) was verified live.
