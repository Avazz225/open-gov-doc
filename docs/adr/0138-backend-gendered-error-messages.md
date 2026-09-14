# 0138 — Backend gender-neutral error messages: the deliberately deferred half of ADR 0119's pass

**Status:** accepted (P33-S4, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 33 Session 4 (accessibility completion, concludes Phase 33, following up on
ADR 0119/P31-S8), affects `auth-service`, `reporting-service`, `notification-service`

## Decision

Four backend `detail=`/error-message strings that surface verbatim in the UI (`ApiError.message` rendered
directly) and used a masculine-generic person noun are reworded using the exact same conventions ADR 0119
already established for the six frontend apps' `de.json` files — no new convention needed:

- `auth-service`'s `GET /users/lookup`/`GET /users/{user_id}` 404s: `"Nutzer {x!r} unbekannt"` →
  `"Person {x!r} unbekannt"`.
- `reporting-service`'s forensic-trace anomaly detector: `"Nutzer {actor!r} hat N Downloads..."` →
  `"Person {actor!r} hat N Downloads..."`.
- `notification-service`'s `POST /notifications` recipient-existence check: `"Unbekannter Empfänger
  {x!r} für Kanal {y!r}"` → `"Unbekannte empfangende Person {x!r} für Kanal {y!r}"` (an adjective +
  "Person" construction, mirroring this codebase's own pre-existing `"ausführende Person"`, not an
  invented compound noun).

A dedicated research pass first swept all 32 backend services (`services/*/src/*/*.py`, plus one level
into `backends/`/`engines/`/`renderers/`/`connectors/` subpackages) for the full family of masculine-generic
role/person nouns (`Nutzer`, `Benutzer`, `Mitarbeiter`, `Bearbeiter`, `Antragsteller`, `Absender`,
`Empfänger`, `Ersteller`, `Prüfer`, `Genehmiger`, `Freigeber`, `Verantwortlicher`, `Ansprechpartner`,
`Vorgesetzter`, `Stellvertreter`, `Teilnehmer`, `Anwender`, `Beauftragter`, `Sachbearbeiter`, and several
more) before writing anything — the four strings above are the complete, real result, not a partial
sample.

## Rationale

- **Same non-mechanical, per-string review as ADR 0119, not a blind find/replace**: every regex hit was
  read in context and judged individually. The overwhelming majority of matches across all 32 services
  turned out to be false positives on closer reading — role display NAMES used as quoted identifiers
  (`'Nutzer-/Rechteverwaltung'`, `permission-service`'s own registered role name, referenced verbatim the
  same way ADR 0119 already excludes quoted role/field names), compound technical/field nouns
  (`Benutzername`, `Absenderinstallation` — a peer DMS installation in `federation-hub-service`'s
  federation, not a human sender, `Empfänger-Domain`-style field labels), internal-only code comments/
  docstrings that never reach a user (`Nutzervorgabe`, `Lizenzgeber`/`Betreiber`), or coincidental
  substring matches on an unrelated word (`Hersteller`/"manufacturer" containing "ersteller"). This mirrors
  ADR 0119's own finding that "the bulk of the ~40 lines the initial regex-based survey flagged turned out
  to be exactly this category on closer reading."
- **`permission-service`'s existing `"Genehmigende Person darf nicht mit der initiierenden Person
  identisch sein"` (approval four-eyes self-approval guard) confirms the "adjective + Person" pattern was
  already independently used in this backend before this session** — this decision extends an existing,
  organically-emerged convention rather than introducing a new one, the same "reuse the project's own
  already-established convention, don't invent one" principle ADR 0119 itself named as a rationale.
- **Why `"Person"` alone for the two straightforward "X unbekannt" cases, dropping the specific noun
  entirely**: the surrounding context (a `username`/`user_id` field, or an `actor` field from the audit
  log) already conveys that this is about a user account — restating "Nutzer" would be redundant once
  neutralized, and a bare `"Person {x!r} unbekannt"` reads naturally in German, exactly like ADR 0119's own
  precedent of dropping "Nutzer" in favor of "Person" for prose about a specific individual (as opposed to
  its substantivized-participle treatment for STANDALONE CATEGORY labels like navigation entries).
- **Why `"empfangende Person"` rather than "Person" alone or an invented noun for the notification-service
  case**: unlike the two lookup 404s, this message's whole point is distinguishing the RECIPIENT from other
  possible roles (e.g. a sender) in a channel-delivery context — dropping the qualifier entirely would lose
  real information the message exists to convey. An adjective ("empfangend", from "empfangen") + "Person"
  reuses the identical grammatical shape as the codebase's own pre-existing `"ausführende Person"`
  (`admin-ui`'s `queryConsole.hint`, from ADR 0119) rather than reaching for a synthetic compound like
  "Empfangsperson", which is understandable but far less established in ordinary German usage.
- **`GET /forensic-trace`'s anomaly string was included even though it's assembled outside any
  `HTTPException`** — it's appended to a plain `list[str]` returned as part of the endpoint's regular
  response body (not an error path), but reaches the UI exactly the same way a `detail=` string would
  (rendered as descriptive text in `admin-ui`'s `ForensicTraceView`), so the same standard applies.
- **Deliberately excludes internal-only comments/docstrings** (e.g. `archival-service`'s/`rendering-
  service`'s `"Nutzervorgabe"`, `license-service`'s `"Lizenzgeber"/"Betreiber"`) — consistent with this
  session's own scope (backend strings that reach the UI verbatim, per the plan's wording and ADR 0119's
  original framing of what it deferred), not a general code-comment language sweep.

## Consequences

- **No schema/API contract change** — these are free-text `detail=`/response-body strings, not typed
  fields; existing frontend code (`ApiError.message` rendering) needed no changes, and no frontend test
  hardcoded the old wording anywhere (confirmed via search across all six apps' test suites).
- **Tests**: no test asserted the exact previous wording in any of the three services (confirmed before
  changing); existing test counts are therefore unchanged (`auth-service` 105, `reporting-service` 57,
  `notification-service` 78, all still green) — this session is a pure text correction, not new behavior,
  so no new test was warranted.
- **Live-verified** against the real, rebuilt running stack: `GET /users/lookup?username=<unknown>` and
  `POST /notifications` with an unknown recipient both return the new wording exactly as written above.
- **This concludes Phase 33** (accessibility completion) — `graphify update .` run at phase end, per the
  standing project convention of running it once per completed phase rather than after every session.
