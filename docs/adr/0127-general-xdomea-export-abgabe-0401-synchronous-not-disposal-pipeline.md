# 0127 — General XDOMEA export: `Abgabe.Abgabe.0401`, synchronous, distinct from the disposal pipeline

**Status:** accepted (P31-S13a, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 13a (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md), gap #12), first of the P31-S13a → 13b → 13c
split ([ADR 0126](0126-xdomea-general-exchange-split-download-upload-not-federation-hub.md)), affects
`archival-service`, `user-ui`

## Decision

`archival-service` gains two new synchronous endpoints, `POST /xdomea/export/documents/{id}` and
`POST /xdomea/export/cases/{id}` (both require a `leser_name` query parameter — the receiving authority),
each returning a downloadable ZIP package (`abgabe.xml` + `dokumente/<paketname>`) built against the real,
newly-vendored XDOMEA 4.0.0 schema for the **`Abgabe.Abgabe.0401`** message ("the complete export of
records objects upon change of jurisdiction between authorities or system changes") — a genuinely
different message than [ADR 0029](0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md)'s
`Aussonderung.Aussonderung.0503` (records disposal), reusing `xdomea.py`'s existing builder/validator
infrastructure but adding new, message-specific building blocks. `user-ui`'s `PreviewPane` gained an
"Export für Behördenübergabe" button (document-level only this session, see "Consequences"); case-level
export is API-only for now.

## Rationale

- **`Abgabe.Abgabe.0401`, not `Aussonderung.Aussonderung.0503`, for general handoff**: confirmed via the
  real, official KoSIT schema (`schema.kdo.de`) that XDOMEA 4.0.0 organizes messages into distinct groups
  per process — Aussonderung (disposal, already implemented), Abgabe (handover on jurisdiction/system
  change), Übermittlung, Geschäftsgang, etc. `Abgabe.Abgabe.0401`'s own schema documentation
  ("Zuständigkeitswechsel zwischen Behörden oder Systemwechseln") is a direct, literal match for this
  session's ask, whereas reusing the disposal message for a non-disposal handoff would misuse a
  domain-specific message for a purpose the standard has a dedicated message for.
- **A real, schema-verified surprise, found only by compiling against the actual vendored schema (not
  assumed from the 0503 code's shape)**: the 0401 message's `Schriftgutobjekt/Vorgang` is typed the
  GENERIC `xdomea:VorgangType` (`xdomea-Baukasten.xsd`), while the 0503 message's is
  `VorgangAussonderungType` (`xdomea-Typen-AussonderungDurchfuehren.xsd`, disposal-specific, requires an
  extra `Kontextobjekt` element `VorgangType` doesn't have). An initial implementation attempt assumed
  these were the same shared type and failed real schema validation immediately — `xdomea.py` therefore
  has two separate Vorgang-builders (`_build_vorgang_aussonderung`/`_build_vorgang_generic`), not one
  reused across both messages; `DokumentOderDokumentMitSchriftstueckType`, by contrast, genuinely IS
  identical across both message families and is shared as originally planned.
- **Synchronous, not the disposal pipeline's async multi-phase state machine**: `case_pipeline.py`/
  `pipeline.py`'s `pending → locked → packaged → verified → released` phases with retry/backoff/encryption
  exist because records disposal is a legally significant, potentially slow, must-not-silently-fail
  operation. A general handoff export is a one-shot, on-demand action — the same reasoning that made
  document-service's own single-document PDF export (`POST /documents/{id}/export`, Phase 28/ADR 0107)
  synchronous applies identically here. No new DB table, no poll loop, no persisted job — mirrors that
  precedent's response shape (`Response(media_type=...)` returned directly).
- **`leser_name` is a required parameter, not a default**: unlike the 0503 message (always addressed to
  "Archiv" — a fixed, known recipient), a general inter-agency handoff has no sensible default reader; the
  caller must name the real receiving authority.
- **A case does NOT need to be closed to be exported** (unlike disposal, which only ever applies to a
  closed circulation folder) — a general handoff can happen to any case, active or closed.
- **A live-verification-found data-integrity distinction, fixed properly**: exporting a real dev-stack case
  whose `CaseDocumentReference` pointed at an already-deleted document produced a `404` mislabeled as "case
  unknown" — misleading, since the case itself was real. Fixed by fetching the case in `main.py` first
  (translating ONLY that lookup's 404 to "case unknown") and passing the already-fetched case dict into
  `build_case_export_package`, which now raises a distinct `ReferencedDocumentMissingError` for a missing
  per-reference document, translated to `409` (a data-integrity condition, not a caller mistake) rather
  than being conflated with the case-not-found path.

## Consequences

- **Case-level export has no `user-ui` entry point yet, document-level does**: `case-service`'s "Case"
  concept (circulation folder) has essentially no dedicated browsing UI anywhere in `user-ui` today (only
  `PoststellePane` references a `caseId` in passing, for mail assignment) — there is no natural existing
  screen to attach a "export this case" button to. Rather than force one in prematurely, this session ships
  the case-export API only; a UI home is a plausible future addition once/if a case browsing view exists.
- **No automatic cross-installation delivery** — the package is a download, per
  [ADR 0126](0126-xdomea-general-exchange-split-download-upload-not-federation-hub.md)'s transport-scope
  decision; getting it to the receiving authority is a manual, out-of-band step.
- **XDOMEA import (P31-S13b) and XJustiz (P31-S13c) remain separate, not-yet-started sessions.**
