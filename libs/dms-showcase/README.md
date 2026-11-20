# dms-showcase

Faux-UI illustrations of OG Doc (login pages, empty states, docs hero), built with the vendored
[Stageshow](https://benji.org/stageshow) React kit. See
[ADR 0237](../../docs/adr/0237-stageshow-adoption-illustrations-and-craft-lens.md) for the scope rules.
The most important rule: **nothing in here is ever used inside a functional screen.** These are
`aria-hidden` pictures with fixed props that never call a service.

Same convention as [`libs/dms-ui`](../dms-ui/README.md): plain files imported by relative path, no npm
package, no workspace tooling.

## Layout

| Path | What |
|---|---|
| `kit/` | Stageshow kit, vendored (see below) |
| `LICENSE`, `NOTICE.md` | Stageshow's MIT license and notices, kept beside the copy |
| `look-ogdoc.css` | The OG Doc look shared by every illustration (since P83-S3; see below) |
| `sync-playground.sh` | Mirrors every illustration folder (any subfolder with a `.tsx`, except `kit/`) into the stageshow playground for filming |
| `user-ui-login/` | `user-ui` login illustration (P83-S2): `UserUiLogin.tsx`, `user-ui-login.css`, `REHEARSAL.md` |
| `admin-ui-login/` | `admin-ui` login illustration (P83-S3): object-type editor, Kennzeichen format and its effect |
| `reviewer-ui-login/` | `reviewer-ui` login illustration (P83-S3): a four-eyes request approved (plays once) |
| `process-designer-login/` | `process-designer` login illustration (P83-S3): a signature task and its required level |
| `migration-console-login/` | `migration-console` login illustration (P83-S3): a finished transfer and its timeline |
| `user-ui-empty-preview/` | `user-ui` empty preview pane still (P83-S4): a selected row and the page it opens; wordless, unframed |
| `user-ui-empty-vorlagen/` | `user-ui` empty Vorlagen still (P83-S4): a folder subtree and the template captured from it; wordless, unframed |

Every Stage sets `className="og og-<name>"`. **`look-ogdoc.css`** (scope `.og`) holds what all
illustrations share (ADR 0237, Decision 3 as amended):
- `--ss-accent`, `--ss-accent-soft`, `--ss-font` and `--ss-radius-*` come from `--dms-*`.
- A product pane and a satellite card (`.og-pane`, `.og-card`) take the login card's material
  (`--dms-surface`, `--dms-border`).
- Text on accent fills uses `--dms-accent-fg`.
- The fade-crop frame (`.og-mask`), the pane chrome (`.og-tabs`, `.og-tab[data-on]`, `.og-bar`,
  `.og-crumbs`, `.og-heading`) and rows (`.og-row`, `.og-name`, `.og-meta`, `.og-code`, `.og-icon`,
  `.og-icon[data-focus]`, `.og-actions`).
- Every `--dms-*` falls back to the kit's own theme token (`var(--dms-surface, var(--ss-surface))`) so
  dark renders correctly in the playground. The exception is the accent: `var(--ss-accent)` as its own
  fallback would be a self-referencing cycle that invalidates the property, so it falls back to a
  literal.
- The kit's own grays are left alone (`references/looks.md`).

Each illustration's own CSS holds only what is specific to its story.

## Vendored kit

- **Source:** `https://github.com/benjitaylor/skills`, folder `stageshow/kit/`, commit
  `299140fafd08df55a9c234e610c3f64af3f45531` (Stageshow v1.1.1).
- **Local patch, the only one:** `kit/brands/` and `kit/parts/BrandMark.*` are **not copied**. That
  artwork is not MIT-covered (`NOTICE.md`), and no third-party mark belongs in an OG Doc illustration.
  Their exports were removed from `kit/index.ts` and `kit/parts/index.ts`, and their `@import` from
  `kit/stageshow.css`.
- **Updating:**
  1. Copy `kit/` from a new pinned commit and re-apply the patch above.
  2. Typecheck against the consuming apps' React types (`tsc --noEmit` in each consuming app).
  3. Re-run the gold calibration from ADR 0237, Decision 5.

  Never update implicitly.

## Using it from an app

Each consuming app needs the four additions from ADR 0237, Decision 2. `user-ui` has them since P83-S2,
and `admin-ui`, `reviewer-ui`, `process-designer` and `migration-console` since P83-S3:

1. `lucide-react` pinned to `0.468.0`.
2. `resolve.modules` in `next.config.mjs`.
3. `react`/`react-dom`/`lucide-react` entries in tsconfig `paths`.
4. `COPY libs/dms-showcase/ libs/dms-showcase/` in the Dockerfile.

Load illustrations with `next/dynamic` (`ssr: false`) behind the app's own gating component (width,
theme). See `apps/user-ui/src/components/LoginIllustration.tsx`.

## Developing and verifying an illustration

Edit here, and film in the stageshow playground (`~/.claude/skills/stageshow`, `npm run dev`):

1. Run `bash libs/dms-showcase/sync-playground.sh` (via `bash`: the repo does not track the executable
   bit, `core.fileMode = false`). It copies every illustration folder to
   `examples/og-<name>/` (the folder name without `-login`, e.g. `og-admin-ui`, `og-user-ui-empty-preview`), with the imports rewritten (`'../kit'` → `'../../kit'`, `'../look-ogdoc.css'` →
   `'./look-ogdoc.css'`). This folder stays the only source; never edit the playground copy. If Vite was
   already running before a *new* folder appeared, restart it, because its example glob only picks up
   files that existed when it started.
2. Film it with `node scripts/film.mjs "<preview>/?capture=og-<name>" --select '#example' --gate`.
   This needs Node from nvm and
   `CHROME_PATH=~/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell`
   (Chrome 153). Not the older `chromium-1140` build: it lacks `text-box` and reports false button-label
   lines.
   Film with `--dark` and `--still --reduced` too, and look at the frames yourself. The gate does not
   check contrast.
3. When done, `bash libs/dms-showcase/sync-playground.sh --clean` removes the playground copies.

The gate policy is in ADR 0237, Decision 5. Every illustration ships a `REHEARSAL.md` beside it.
