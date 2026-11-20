# Rehearsal: user-ui login illustration (P83-S2)

**P83-S3 refactor:** the shared parts (token mapping, fade-crop frame, pane chrome, rows) moved into
`../look-ogdoc.css`. The Stage is now `className="og og-login"`, the tab carries `data-on`, and the icons
use `.og-icon`/`[data-focus]`. The picture is unchanged. After the refactor it was re-filmed (Chrome 153):
`--gate` on the motion gave `rehearsal passed`, and `--still --reduced` light/dark gave
`no-issues-detected`. Live it was re-measured in the rebuilt image: pane top = card top = 286.

**Gold:**
- Started from `upload`: the drag-in, the drop-zone highlight, the upload row in a `Collapse` with a
  `Meter` that settles via `States`, and the `DragImage` hit-testing.
- After the fresh-eye review, the framing moved to `fade-crop`: a pane on the page itself, whole at the
  top and sides, dissolving at its foot through a mask on a wrapper.

**What changed vs. the gold:**
- **No OS window.** The pane is OG Doc's own Explorer pane: the `Explorer` tab, the `Start ›
  Eingangsrechnungen` breadcrumb and the primary `Hochladen` button. Its material is the login card's
  (`--dms-surface`, `--dms-border`, 8px radius, the card's shadow).
- **No plate.** The page is the ground.
- **The file enters from past the stage's left edge.** The desktop is implied, so the frame keeps one
  gutter. The drag image fades in only once it is fully inside the stage.
- **No `Count`.** There is no size or date column, because the real Explorer has neither. That also
  removes the gold's own `appears with no entrance` fault.
- **The payoff is the reference number.** It is plain text, in the accent at weight 600, and opens its
  own width (0fr → 1fr) before the filename, as `apps/user-ui/src/lib/kennzeichen.ts` shows it
  ("<Kennzeichen> <title>"). Two real rows below already carry 2026-113 and 2026-112, so the new number
  reads as joining the sequence.
- **Look.** `--ss-accent`, `--ss-accent-soft`, `--ss-font` and the radii come from `--dms-*`.
  `--dms-accent-fg` sets the text on accent fills (ADR 0219).

**Beats** (play once, about 5.5s, then the payoff holds; `poster` = the settled row):
1. 0.5s: the file is carried in and the list lights up as a drop zone.
2. About 2.0s: dropped at the top of the list, where the upload row opens right under it.
3. 2.0–3.6s: the bar fills.
4. 4.1s: the bar gives way to the row's actions and the tint fades.
5. 4.7s: the payoff. "2026-114" grows in before the filename.

The cursor fades after 1.4s idle. The story is shorter than the skill's 10–16s on purpose: people on a
login page are on their way in, and a once-played scene ends on its result.

**Real strings:** Explorer, Start, Eingangsrechnungen, Hochladen, Rechnung_Mai.pdf, 2026-114, plus the
two neighbour rows (number + filename). Everything else is `Bar` filler that fades with the pane. The
pane, root and upload labels come from the same i18n keys the real Explorer uses
(`workspace.explorerPanelTitle`, `folderBrowser.rootLabel`, `explorer.toggleUpload`).

**Captured:**
- Stage width 720, its only rendered width. The host renders nothing below 1200px viewport (ADR 0237),
  so no `compact` design exists and 390 is not a shipping width.
- Light and dark, with the reduced-motion poster in both.
- Live in the built `user-ui` image at 1400 light/dark/high-contrast/reduced/EN, 1199, 1200 and 390
  (the last two only to confirm the gate).
- A real login afterwards, still working.

**Gate** (`film.mjs --gate`, CHROME_PATH = Chrome for Testing 153, see ADR 0237 Decision 5):

```
rehearsal passed (necessary, not sufficient: references/verify.md and the fresh eye in review.md still stand)
```

The result is the same for `--dark`. The stills (`--still --reduced`, light and dark) gave
`no-issues-detected`. No line is excused under ADR 0237's gold-proven exception.

**Film** (lines on the way, frame, then fix):
- `framing: real words … "Rechnung_Mai.pdf" are cut by the crop` (0.0–0.5s, drag image entering from
  the edge). Fixed: the drag image only shows once its whole box is inside the stage.
- `translucent icon` on the folder. Fixed: opaque fill.
- `clipped: near aligned: "Explorer" and "Start" start 4.0px apart`. Fixed: both on one 16px edge.
- `layout: span.ss-button__label is 15px high but … needs 17px`. This was an environment artifact:
  Chromium 130 (Playwright build 1140) lacks `text-box: trim-both`, and the unmodified `buttons` specimen
  shows the same line there. It is gone with Chrome 153. Firefox also lacks `text-box` today, so a
  Firefox user sees the button's label 1–2px lower; not visible as a fault.
- Not a gate line, found by eye: the dark poster in the playground showed a white pane under light ink,
  because the `--dms-*` fallbacks were light-only. Fixed: the fallbacks follow the kit's theme tokens
  (`var(--ss-surface)`, `var(--ss-line)`). The real app was never affected, since it defines `--dms-*`
  per theme.
- Also by eye: white text on dark theme's `#60a5fa` button. Fixed: `--dms-accent-fg`.

**Review** (fresh-eye subagent, `references/review.md`; first version) and response:
1. Plate behind the window, a "hole" in dark mode. Fixed: fade crop, no plate.
2. Stage and card out of line, radii differ. Fixed: the pane top is aligned exactly with the card top
   (host `-mt-6` compensates the 24px shadow room, measured live at y=286 for both), and both use 8px
   radius and the same material.
3. The right bleed had no story. Moot: no bleed, the pane is whole across.
4. One real row over a skeleton. Fixed: two real neighbour rows, and filler only below them.
5. The prefix rhyme didn't read. Fixed by 4: real numbers 2026-113/112.
6. A pill the product doesn't have. Fixed: plain text, as `formatDocumentTitle` renders it.
7. The strongest mark sat on filler (blue folder). Fixed: neutral folder, and the new row's icon at
   full `--ss-icon` strength.
8. macOS chrome on a web product. Fixed: OG Doc's own pane chrome.
9. The drop was not connected to the new row. Fixed: the drop is aimed at the top of the list, where
   the row opens.
10. The drag image was translucent. Fixed: opacity 1.
11. Cursor idle for 4s. Fixed: `idle: 1400`.
12. Pill shape foreign. Moot (see 6).
13. Traffic lights crowd the breadcrumb. Moot (see 8).

**Assumptions:**
- Users recognize the Explorer pane from the product itself.
- "Kennzeichen" numbering per folder class is configured in typical installations. The picture shows the
  feature, not a guarantee that every installation numbers invoices.

**Unresolved:**
- The real `user-ui` still uses emoji icons (P83-S1 punch list item 2). The illustration deliberately
  uses Lucide, the set item 2 is expected to adopt. Until that session lands, the picture's icons are
  cleaner than the app's.

**Not checked:**
- Frame-by-frame at 40–80ms around the drop and the number's width reveal. The 500ms contact sheet and
  the gate's sampling (36 gaps over 27ms on this machine's software rendering: `incomplete`, not judged)
  are all there is.
- Actual Firefox and Safari rendering.
- A second fresh-eye review after the revision. The responses above were verified by re-filming and live
  screenshots, not by the reviewer.
