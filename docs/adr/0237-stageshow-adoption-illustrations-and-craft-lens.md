# 0237 — Stageshow adoption: faux-UI illustrations in `libs/dms-showcase`, craft rules as an audit lens for the real UI

**Status:** accepted
**Context:** P83-S1 (Phase 83, assessment session). The user asked for another UI rework pass driven by
the **Stageshow** skill (`benjitaylor/skills/stageshow` v1.1.1, MIT, installed at
`~/.claude/skills/stageshow`, pinned commit in its `.installed-from`). Stageshow is not a design system for
functional application UI. It builds *faux product UI*: framed, simplified illustrations of a product, as a
still or a short cursor animation. It ships a React kit (`kit/`: `Stage`, `Act`, `Window`, `Row`, `Bar`, …)
and a filming/verification tool (`scripts/film.mjs --gate`). This ADR fixes how the project uses it.
Phases 48/49/75/77/81 already settled how the real controls look (ADR 0168/0219/0226/0227, `libs/dms-ui`);
nothing here reopens that.

## Decision 1 — Two separate uses, never mixed

1. **Craft lens on the real UI.** Stageshow's still-frame rules (`references/craft.md`, `framing.md`) and
   its five failure modes (too much, wireframe, boxed, flat, bland) are used as an **audit lens** on the six
   real apps. Fixes are made in the existing Tailwind/`--dms-*` formula. The P83-S1 punch list is recorded
   in `IMPLEMENTATION_PLAN.md` under P83-S1.
2. **Illustrations** are built with the kit, to the skill's full workflow, in a fixed list of slots
   (Decision 4).

**No kit part is ever used inside a functional screen.** Kit parts are illustration geometry: `aria-hidden`,
non-interactive, with fixed props. They are not accessible form controls, and Phase 33's accessibility
guarantees apply to the real controls only.

## Decision 2 — Kit location: `libs/dms-showcase/`, not per-app copies

The kit and all illustrations live in **`libs/dms-showcase/`**: `kit/` vendored from the pinned commit, plus
one folder per illustration and a shared look file. This is the same plain-relative-import convention as
`libs/dms-ui` (ADR 0168). ADR 0006's "duplicate on purpose" applies to shared *domain logic*; illustrations
contain none, and five copies of a ~700 KB kit would drift.

**Verified, not assumed** (P83-S1, on a scratch copy of `user-ui`; the repo itself was not modified):

- The kit typechecks against the apps' React 19 types (`@types/react` 19, TypeScript 5.9.3) with
  **0 errors**. It uses no React-18-only API (no `defaultProps`, `findDOMNode`, `ReactDOM.render`).
  `lucide-react` 0.468 declares `react ^19` as a peer.
- `next build` (Next 15.5, `output: "export"`) compiles a page importing the kit from
  `../../libs/dms-showcase/kit`. Next's own `react` alias resolves React from outside the app dir. Every
  other bare import needs help. Each consuming app needs **four additions**:
  1. `lucide-react` (pinned `0.468.0`, the kit's version) in its `package.json`.
  2. `webpack: (config) => { config.resolve.modules = [...(config.resolve.modules ?? ["node_modules"]), path.resolve("node_modules")]; return config }`
     in `next.config.mjs`. Without it the build fails with `Can't resolve 'lucide-react'`.
  3. `compilerOptions.paths` entries for `react`, `react/*`, `react-dom`, `react-dom/*` and `lucide-react`
     pointing into the app's own `node_modules`. Without them `next build`'s typecheck fails with
     `Cannot find module 'react-dom'` for files under `libs/`.
  4. `COPY libs/dms-showcase/ libs/dms-showcase/` in its Dockerfile, next to the existing `libs/dms-ui` line.
- Cost: the test page (`Stage` + `Window` + `Icon`) was **23 kB** first-load. Illustrations are
  `next/dynamic`-loaded (`ssr: false`), so a login page never waits on them.

**Vendoring rules:**

- Copy `kit/` **without** `kit/brands/`, and never `assets/app-icons.ts`. That artwork is not MIT-covered
  (`NOTICE.md`), and no third-party mark belongs in an OG Doc illustration. Remove the `brands` and
  `BrandMark` exports from `kit/index.ts` and record that one local patch in
  `libs/dms-showcase/README.md`.
- Keep `LICENSE` and `NOTICE.md` beside the copy.
- Code comments follow the project rule (English). The kit's own files stay as upstream.

## Decision 3 — Look mapping, themes, motion

- One look file, `libs/dms-showcase/look-ogdoc.css`, maps Stageshow tokens onto `--dms-*` per
  `references/looks.md`:
  - `--ss-accent` → `--dms-accent`
  - `--ss-accent-soft` → `--dms-accent-bg`
  - `--ss-surface` → `--dms-surface`
  - `--ss-canvas` → `--dms-bg`
  - `--ss-text-strong`/`--ss-text` → `--dms-fg`/`--dms-surface-fg`
  - `--ss-line` → `--dms-border`
  - `--ss-success`/`--ss-danger` → `--dms-success`/`--dms-danger`
  - `--ss-font`/`--ss-mono` → `--dms-font-family`/`--dms-font-family-mono`
  - `--ss-radius-*` → `--dms-radius-*`

  The `Stage` theme follows the app's resolved `data-theme` (`light`/`dark`).

  **Amended in P83-S2 (as built):**
  - ~~There is no separate `look-ogdoc.css` yet.~~ **Since P83-S3** `libs/dms-showcase/look-ogdoc.css`
    (scope `.og`) holds the shared look, the fade-crop frame, the pane chrome and rows, and the satellite
    card. Each illustration's CSS holds only its own story. The accent alone falls back to a literal:
    `var(--ss-accent)` as its own fallback is a self-referencing cycle that invalidates the property.
  - The mapping is narrower than listed above: `--ss-accent`, `--ss-accent-soft`, `--ss-font` and the
    radii only. The kit's text inks and grays are left alone, as `looks.md` advises.
  - Two additions came from live review. A pane drawn as product UI takes the login card's own material
    (`--dms-surface`, `--dms-border`), so both read as one figure language. Text on an accent fill takes
    `--dms-accent-fg` (ADR 0219), overriding the kit's fixed white with a plain class, which beats the
    kit's `@layer stageshow`.
  - Every `--dms-*` reference falls back to the kit's own theme token (`var(--dms-surface,
    var(--ss-surface))`), never to a light-only literal. Otherwise a dark stage in the playground renders
    light ink on a white pane, which the gate does not catch (contrast is not one of its checks).
- **High-contrast theme: illustrations are not rendered at all.** A faux UI carries no information the
  real form doesn't. A kit look tuned for high contrast would be a second design system for decoration.
- **`prefers-reduced-motion`:** the `poster` frame is shown, still.
- **Language:** the illustrations' 3–7 real strings come from each app's existing `i18n/de.json`/`en.json`.
- **Accessibility:** the stage is `aria-hidden="true"`; the host adds nothing to the page outline (the
  kit's titles are `div`s, never headings, per `craft.md`).

## Decision 4 — Illustration slots (what gets built, what does not)

| Slot | Medium | Layout / closest gold | Story (real strings only on this) |
|---|---|---|---|
| `user-ui` login (**built P83-S2**, see its `REHEARSAL.md`) | Plays once, then holds the poster | ~~Corner bleed~~ **Fade crop**, OG Doc's own Explorer pane instead of an OS window / `upload` motion | ~~… class chip … "Freigegeben"~~ A PDF is dropped into "Eingangsrechnungen" → the upload row settles → the server-assigned Kennzeichen "2026-114" grows in before the filename, after real rows 2026-113/112. Changed during the build: a status pill or virus-scan step would have shown things the product's explorer does not display. The Kennzeichen is real and shown exactly this way (`lib/kennzeichen.ts`). |
| `admin-ui` login (**built P83-S3**) | Still | **Fade crop** + a satellite straddling the pane's edge, one leader / `panel-annotation` | ~~A retention setting with a leader to the folder it governs~~: the product shows no such per-folder link. **Instead:** the Kennzeichen format `{YYYY}-{Laufende_Nummer}` of the class "Eingangsrechnung", with a level leader to its effect, the row `2026-114 Rechnung_Mai.pdf` as user-ui shows it. Both ends are real, and it ties to the user-ui story. |
| `reviewer-ui` login (**built P83-S3**) | ~~Short loop~~ **Plays once**, then holds | **Fade crop** / `form-error` + `dark-hero` | As planned: "Freigeben" pressed → own pill "Offen" → "Freigegeben", then the actions fold their width away so the pill rests at the row's end, in line with the waiting requests' pills. The real native `confirm()` step is left out. |
| `process-designer` login (**built P83-S3**) | Still | **Fade crop** + the properties panel straddling the pane's edge (the real designer docks it right) | Start → "Rechnung prüfen" → "Freigabe zeichnen" (selected, bpmn-js's own outline) → end; panel: "Signatur erforderlich" on, "Mindestniveau" AES. |
| `migration-console` login (**built P83-S3**) | Still | **Fade crop** + the "Zeitverlauf" card straddling the pane's edge below the row | A finished transfer "2/2 kopiert, 2 verifiziert", "Freigegeben", and its real phase timeline (Angelegt … Freigegeben, "Löschung geplant" still ahead). |

**Applies to all five login illustrations (P83-S2/S3):** fade crop on the page itself, OG Doc's own pane
chrome (never an OS window), the login card's material, the pane's top edge level with the card's, and
labels from each app's own i18n keys wherever the real UI already has them.
| `user-ui` empty preview pane ("Kein Dokument ausgewählt") (**built P83-S4**) | Still, gray art per `craft.md` (empty states are the one place for it), **wordless** | ~~Fade crop~~ **Unframed** (see below) / `static-card` | A selected list row (the one accent) and the page it opens; no cursor |
| `user-ui` empty "Vorlagen" (**built P83-S4**) | Still, gray art, **wordless** | **Unframed** / `stacked-cards` | A folder subtree, and in front of it the template captured from it: its mark (the one accent) and the same shape as dashed outlines, because templates capture structure only |
| `README.md`/docs hero | Still (`film.mjs --still`) | Wide hero / `linear` | OG Doc explorer + preview, realistic fidelity |

**Not built:**

- **`office-addin`:** its task pane is the whole UI at ~320–350 px. The skill's floor for a demo is ~320 px,
  and the login is a one-field step inside Word.
- **Onboarding/first-run:** no first-run mechanism exists in any app. An illustration would need a new
  feature to host it, which is out of scope.
- **The remaining empty states** (Papierkorb, Favoriten, Team-Aufgaben, Umlaufmappen filters): text fixes
  from the punch list only. Illustrating every empty state is the "too much" failure mode at page level.

**Empty-state stills (P83-S4):**
- **Wordless.** Inside a functional pane, a real filename would read as if a document were actually
  there; the pane's unchanged sentence carries the meaning.
- **`unframed`.** `framing.md` leaves unframed to the developer; this is that call. A spot illustration
  in a user-resizable dockview pane has no page edge to bleed against, and a crop there reads as a
  rendering fault.
- **Gated by the width inside the pane, not the viewport.** `ResizeObserver` measures the empty-state
  block itself, and the picture shows from ≥ 304px of content width. The 320px stage then never scales
  below 0.95, so 1px lines and 14px icons stay crisp, and no `compact` design is needed (`framing.md`: "a
  card that already fits needs none"). The first threshold, 280px of *pane* width, was corrected by the
  review: it meant about 0.75 scale and soft lines.
- **Each stage is 320×192**, so every edge keeps a margin at least as deep as the shadows. That is
  `framing.md`'s condition for unframed; the first 176px height clipped them.

**Breakpoint:** login illustrations render side-by-side with the form only at ≥ ~~1024~~ **1200** px. That
is amended in P83-S2: a 720px stage, a 48px gap and the 384px card fit at 1:1 only from 1200px, and below
that the stage would be scaled down. Below that the form
stands alone. Every illustration that renders below 600 px ships a `compact` design at 358 px
(`framing.md`). The two in-pane empty-state stills do, because the pane can be narrow.

## Decision 5 — Gate policy, calibrated in this environment

Running the tool here takes two things:

- Node from nvm: `~/.nvm/versions/node/v24.14.1/bin` (not on the default `PATH`).
- ~~`CHROME_PATH=~/.cache/ms-playwright/chromium-1140/chrome-linux/chrome`~~. **Corrected in P83-S2:** use
  `CHROME_PATH=~/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell`
  (Chrome for Testing 153).
  - The 1140 build is Chromium 130, which lacks `text-box: trim-both`. The kit uses it on every `Button`
    label, so the old browser reports a false `layout: span.ss-button__label is … high but … needs …`
    line on every button. The unmodified `buttons` specimen shows it too, and it disappears with 153.
  - The gold calibration below was re-run with 153. The results are the same, apart from that line.

Unmodified gold examples, filmed with `--gate` at their own width and at 390:

| Gold | Wide | 390 | Lines |
|---|---|---|---|
| `static-card` (still) | passed | passed | 2 non-blocking notes (`clipped` 1px, `lopsided` row) |
| `loading` | passed | **failed** | 2 × `framing` (window short of the edge; narrower than tall). Genuine: `loading` has no `compact` design |
| `upload` | **failed** | **failed** | `appears with no entrance` on a `Count` digit strip (plus its `jump` lines, marked `[done]`); `framing` gutter mismatch |
| `hero-demo` | **failed** | — | caret `jump`, dashed edge crawl, `vanishes with no exit`, `selection drift` |

The rules follow from that table:

- **Stills must pass outright** at every width they render at.
- **Animations must end `rehearsal passed`.** The only exception is a line in a fault class that the
  table shows on an unmodified gold *from the same kit part*: `Count`/`Roll` digit-strip
  `jump`/`appears with no entrance`, caret `jump`, `selection drift`. Each such line is listed in the
  illustration's `REHEARSAL.md`.
- **`framing`, `cut by the crop`, `tight inset`, `touching`, `mixed icons`, `invented mark` and every other
  class are never excused.** They are composition faults we would own.
- **A line that seems wrong is still answered by the composition, not excused** (P83-S3). Example: a
  tab strip directly over a bar with a lone heading drew `tight stack … 2.7px of ink`, unchanged when
  the bar's padding went from 12 to 18px, so the number did not track the visible gap. In both cases the
  real fix was a better picture. In `reviewer-ui` the heading only repeated the active tab ("Say it
  once"), so it was dropped. In `admin-ui` the real app has no tab strip at all, so the tabs became a
  breadcrumb.
- Filming runs against the stageshow playground during development: copy the illustration to
  `examples/<name>/` and use `?capture=<name>`, the skill's "quickest loop". It also runs against the built
  app page once integrated.

## Decision 6 — Narrow widths become a goal (user decision, 2026-11-20, recorded in P83-S5)

The P83-S1 punch list left item 12 (the real apps are unusable at 390px) as an open question for the
user, because no concept section asks for phone support in the back-office apps. The user decided: yes,
narrow-width/mobile support is worth having for the back-office apps too.

What this means here:
- P83-S9 assesses all apps at 390/768px and records the patterns in their own ADR. P83-S10+ implements
  them.
- Every fix from P83-S5 on is built so it already holds when narrow. The first instance is S5's toolbar
  pattern: whole buttons wrap, labels never do.
- The illustrations keep their current rules until S9 decides otherwise. Logins render only at ≥ 1200px,
  so the card stands alone on small screens; empty-state stills are gated by pane width. S9 re-checks
  whether a narrow login should get a `compact` illustration.
- **Docs hero built in P83-S14** (`libs/dms-showcase/readme-hero/`, PNGs in `docs/assets/`, shown in
  `README.md`), with a real compact design. That closes Decision 4's slot list.
- **Settled in P83-S9 by [ADR 0238](0238-narrow-width-patterns-for-all-apps.md):** the narrow-width
  patterns for all apps and their sessions (P83-S10–S13); no compact login illustration.

## Consequences

- Phase 83's remaining sessions follow `IMPLEMENTATION_PLAN.md` as amended by P83-S1:
  - S2 sets up `libs/dms-showcase` and builds the `user-ui` login.
  - S3 builds the other four logins.
  - S4 builds the two empty-state stills.
  - S5+ apply the punch list.
  - The last session builds the README hero and closes the phase.
- The kit is a vendored third-party dependency. Updating it is a deliberate copy from a new pinned commit,
  re-running `tsc` and the gold calibration above. It never updates implicitly.
- Phase 81's "no Node in this environment, drive Chrome over raw CDP from Python" workaround is obsolete.
  Node and `playwright-core` work on the host now (P83-S1 drove all 96 audit screenshots this way).
