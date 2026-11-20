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
| `user-ui-login/` | `user-ui` login illustration (P83-S2): `UserUiLogin.tsx`, `user-ui-login.css`, `REHEARSAL.md` |

Each illustration carries its own OG Doc look block at the top of its CSS (ADR 0237, Decision 3 as
amended):
- `--ss-accent`, `--ss-accent-soft`, `--ss-font` and `--ss-radius-*` come from `--dms-*`.
- A product pane takes the login card's material (`--dms-surface`, `--dms-border`).
- Text on accent fills uses `--dms-accent-fg`.
- Every `--dms-*` falls back to the kit's own theme token, never a light-only literal, so light and dark
  both render correctly in the stageshow playground, where `--dms-*` does not exist.
- The kit's own grays are left alone (`references/looks.md`). Once a second illustration exists, move the shared block into a
`look-ogdoc.css` here rather than copying it a third time.

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

Each consuming app needs the four additions from ADR 0237, Decision 2. `user-ui` has them since P83-S2:

1. `lucide-react` pinned to `0.468.0`.
2. `resolve.modules` in `next.config.mjs`.
3. `react`/`react-dom`/`lucide-react` entries in tsconfig `paths`.
4. `COPY libs/dms-showcase/ libs/dms-showcase/` in the Dockerfile.

Load illustrations with `next/dynamic` (`ssr: false`) behind the app's own gating component (width,
theme). See `apps/user-ui/src/components/LoginIllustration.tsx`.

## Developing and verifying an illustration

Develop in the stageshow playground (`~/.claude/skills/stageshow`, `npm run dev`):

1. Copy the illustration to `examples/<name>/index.tsx`, with the kit import as `'../../kit'`.
2. Film it with `node scripts/film.mjs "<preview>/?capture=<name>" --select '#example' --gate`.
   This needs Node from nvm and
   `CHROME_PATH=~/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell`
   (Chrome 153). Not the older `chromium-1140` build: it lacks `text-box` and reports false button-label
   lines.
   Film with `--dark` and `--still --reduced` too, and look at the frames yourself. The gate does not
   check contrast.
3. Copy it back here when it passes, with the import as `'../kit'`.

The gate policy is in ADR 0237, Decision 5. Every illustration ships a `REHEARSAL.md` beside it.
