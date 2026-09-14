# 0135 — Accessibility audit of the five non-`user-ui` frontend apps: real findings, not the assumed ones

**Status:** accepted (P33-S1, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 33 Session 1 (accessibility completion, following up on ADR 0119/P31-S8), affects
`admin-ui`, `reviewer-ui`, `migration-console`

## Decision

The Phase 32+ plan's premise for this session — "port `user-ui`'s classification/conflict/redaction badge
icon+`aria-label` fix (ADR 0119) to the other five apps, `admin-ui`'s `ObjectTypeEditor.tsx` `.badge.
classified` being the known first case" — does not hold against the current code: `ObjectTypeEditor.tsx`
has no such badge (only a plain `<select>` for the object type's default classification level), and
`reviewer-ui`/`process-designer`/`migration-console`/`office-addin` have no document classification/
conflict/redaction UI at all. A fresh, code-level audit (confirmed empirically, not assumed from the plan)
instead found three genuinely real, but different, accessibility bugs across three of the five apps, all
fixed in this session:

1. **`admin-ui`'s `LayoutDesigner.tsx`**: the within-row field-reorder buttons (`◀`/`▶`) had no `aria-label`
   at all — only the raw glyph as both visible and accessible content — unlike their sibling row-level
   "Nach oben"/"Nach unten" buttons two lines above, which already had full text labels. Fixed with
   `aria-label={t("layoutDesigner.moveFieldLeft"/"moveFieldRight")}`.
2. **`reviewer-ui`/`migration-console`'s `high-contrast` theme**: `--dms-accent-bg` was set to the
   identical `#ffff00` as `--dms-accent` itself, rendering `.badge-pending` (the dry-run badge in
   `TransferConsole.tsx`, the signature badge in `reviewer-ui`'s `TaskList.tsx`/`InstanceDetail.tsx`) as
   yellow text on a yellow background — completely illegible, a much more severe failure than ADR 0119's
   original color-collision bug (there, both meanings were merely confusable; here, one is unreadable
   outright). Fixed by setting `--dms-accent-bg: #000000` in the `high-contrast` block, matching
   `--dms-danger-bg`/`--dms-success-bg`'s already-correct sibling pattern.
3. **Same two apps, missing `.badge` high-contrast border rule**: unlike `user-ui`/`admin-ui` (which
   already have `:root[data-theme="high-contrast"] .badge { border: 1px solid currentColor; }` since ADR
   0119), `reviewer-ui`/`migration-console` never had this rule — `.badge-approved`/`.badge-rejected`
   silently lost their pill shape (background flattens to the page background) in `high-contrast`, even
   though their text stayed legible via distinct hues. Fixed by adding the identical rule.

## Rationale

- **Verified the plan's premise before touching code, rather than force-fitting speculative work to match
  it**: the plan's specific file/line reference (`ObjectTypeEditor.tsx`) was checked directly and does not
  match current code; a broader grep across `classification_level`/`is_conflict`/`derivation_type`/`.badge`
  usage in all five apps confirmed no equivalent of `user-ui`'s original bug exists anywhere else. Reported
  this finding to the user rather than either declaring the session vacuously complete or inventing
  unrelated changes to appear productive — same "verify, don't estimate" practice this project has applied
  before (e.g. P32-S1's ADR 0089 blast-radius re-mapping).
- **User chose a deeper audit over declaring the session done or a blanket icon-everywhere polish pass**:
  given the specific premise didn't hold, a broader but still targeted audit (WCAG 1.4.1 "use of color
  alone", icon-only controls without a label, and each app's own `high-contrast` CSS block) was the
  appropriate scope — narrower than auditing literally everything, wider than the one named file.
- **Every existing `.badge ok`/`down`/`badge-pending` status pill already carrying its own distinct
  descriptive text per state was correctly judged NOT a violation** (`DelegationsAdmin.tsx`,
  `InstallationManager.tsx`, `RegistryOverview.tsx`, `StorageGuard.tsx`, `ArchivalTransfersView.tsx`,
  `LicenseStatusView.tsx`, `TaskList.tsx`'s/`TransferConsole.tsx`'s "Testlauf"/"Signatur" badges) — color is
  supplementary there, not the sole channel, unlike `user-ui`'s original bug where a bare classification
  value or an ambiguous glyph relied on color to distinguish otherwise-identical-looking badges.
- **`ArchivalTransfersView.tsx`'s `{transfer.encrypted ? "✓" : "—"}` table cell judged acceptable, not
  fixed**: no color is involved (plain Unicode text, not a CSS-only signal), and it sits under a properly
  labeled `<th>` — a table-navigation-mode screen reader announces header+cell together. A trivial
  robustness improvement (swapping to `t("common.yes")`/`t("common.no")`) was identified but left
  unfixed — a nice-to-have, not a real accessibility violation, and out of this session's narrower bug-fix
  scope.
- **`process-designer`/`office-addin` confirmed to have nothing to fix in this category**: `process-
  designer` defines an unused `.badge` base class (no component ever applies it) — a dead-code gap, not a
  live bug; every button carries a real text label. `office-addin` deliberately has no `data-theme`/theme
  switcher at all (it follows Office's own `prefers-color-scheme` instead, an existing, documented design
  choice) — there is no mechanism to ever reach a `high-contrast` state in this app, so its absence is not
  a gap.
- **`reviewer-ui`/`migration-console` share byte-identical `globals.css` structure** (same variable names,
  same `high-contrast` block, same `.badge`/`.badge-pending`/`.badge-approved`/`.badge-rejected` classes) —
  both received the identical fix for the identical reason, not independently rediscovered per app.

## Consequences

- **No new dependency, no new component, no CSS variant renamed** — both fixes are narrow, targeted
  corrections to existing tokens/rules, matching the size of the actual bugs found.
- **`reviewer-ui`/`migration-console`'s `high-contrast` theme now renders every badge legibly and with a
  visible pill shape** — previously a `high-contrast`-theme user (the exact audience this theme exists for)
  could not read the dry-run/signature-pending badge at all.
- **Tests**: `admin-ui` +1 (`layout-designer.test.tsx`: field-reorder buttons have the correct accessible
  names via `getByRole("button", {name: ...})`, and clicking one actually swaps the two fields' order — a
  minimal, existing-pattern-following functional test, since no CSS-only visual regression test exists in
  this project for color contrast). No new tests for the `reviewer-ui`/`migration-console` CSS fix — this
  project has no visual-regression/computed-style test harness for any of the six apps' theme CSS
  (`theme-context.test.tsx` files test the theme-selection *logic*, not rendered contrast), consistent
  with every prior high-contrast fix in this codebase (ADR 0119's own high-contrast border fix likewise
  added no new test). Live-verified instead: the rebuilt, deployed CSS/JS bundles of all three apps were
  fetched directly and confirmed to contain the fixed tokens/rules/strings.
- **This session's scope stayed exactly what the deeper audit found** — no speculative additional "while
  we're at it" accessibility changes beyond the three confirmed real bugs, consistent with this project's
  practice of bounded, verified sessions over broad unscoped sweeps.
