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
- **High-contrast theme: illustrations are not rendered at all.** A faux UI carries no information the
  real form doesn't. A kit look tuned for high contrast would be a second design system for decoration.
- **`prefers-reduced-motion`:** the `poster` frame is shown, still.
- **Language:** the illustrations' 3–7 real strings come from each app's existing `i18n/de.json`/`en.json`.
- **Accessibility:** the stage is `aria-hidden="true"`; the host adds nothing to the page outline (the
  kit's titles are `div`s, never headings, per `craft.md`).

## Decision 4 — Illustration slots (what gets built, what does not)

| Slot | Medium | Layout / closest gold | Story (real strings only on this) |
|---|---|---|---|
| `user-ui` login | Loop, plays once in view, then holds the poster | Corner bleed / `upload` (+ `hero-demo` for the cursor) | A PDF dragged into a folder → upload row settles → class chip assigned → status "Freigegeben". Poster: the released row. |
| `admin-ui` login | Still | Panel over product / `panel-annotation` | A retention setting ("Aufbewahrung 10 Jahre") with one leader to the folder it governs |
| `reviewer-ui` login | Short loop | Pinned bottom bleed / `form-error` + `dark-hero` (status changes on its own pill) | A four-eyes request: "Freigeben" pressed → pill "Offen" → "Freigegeben" |
| `process-designer` login | Still | Pinned bottom bleed / `annotations` | Three BPMN shapes, one signature task in focus with its "AES" level |
| `migration-console` login | Still | Satellite focus / `loading` | One transfer row "2/2 kopiert, 2 verifiziert" over a quiet list |
| `user-ui` empty preview pane ("Kein Dokument ausgewählt") | Still, gray art per `craft.md` (empty states are the one place for it) | Fade crop / `static-card` | A selected list row and the preview it opens; no cursor |
| `user-ui` empty "Vorlagen" | Still | Stacked cards / `stacked-cards` | A folder subtree over the template made from it |
| `README.md`/docs hero | Still (`film.mjs --still`) | Wide hero / `linear` | OG Doc explorer + preview, realistic fidelity |

**Not built:**

- **`office-addin`:** its task pane is the whole UI at ~320–350 px. The skill's floor for a demo is ~320 px,
  and the login is a one-field step inside Word.
- **Onboarding/first-run:** no first-run mechanism exists in any app. An illustration would need a new
  feature to host it, which is out of scope.
- **The remaining empty states** (Papierkorb, Favoriten, Team-Aufgaben, Umlaufmappen filters): text fixes
  from the punch list only. Illustrating every empty state is the "too much" failure mode at page level.

**Breakpoint:** login illustrations render side-by-side with the form only at ≥ 1024 px. Below that the form
stands alone. Every illustration that renders below 600 px ships a `compact` design at 358 px
(`framing.md`). The two in-pane empty-state stills do, because the pane can be narrow.

## Decision 5 — Gate policy, calibrated in this environment

Running the tool here takes two things:

- Node from nvm: `~/.nvm/versions/node/v24.14.1/bin` (not on the default `PATH`).
- `CHROME_PATH=~/.cache/ms-playwright/chromium-1140/chrome-linux/chrome`.

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
- Filming runs against the stageshow playground during development: copy the illustration to
  `examples/<name>/` and use `?capture=<name>`, the skill's "quickest loop". It also runs against the built
  app page once integrated.

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
