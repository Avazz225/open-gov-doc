# Rehearsal: README/docs hero still (P83-S14)

**Gold:** `static-card` (quiet hierarchy, one satellite card, real words only on the subject), at
`framing.md`'s *wide hero* size (960 × 440, inset top, off the right edge, fading at the foot). The house
frame (fade crop, pane chrome, rows, card material) comes from `../look-ogdoc.css`, as for the logins.

**Story** (still): OG Doc's documents workspace. On the left is the Explorer group with an invoice folder
and the selected `2026-114 Rechnung_Mai.pdf` among its numbered neighbors. On the right is its preview
group: the version picker and the invoice page with vendor, invoice number and total. A card over the
preview's empty well says the signature is valid (SES). One picture of what the system is for: filed,
numbered, previewed, signed.

**Compact** (358 × 420, below 600px): a redesign, not a squeeze. Only the Explorer group remains, the root
crumb and the button label give way, and the card straddles the group's right edge over the filler rows.

**Host:** `README.md` through a `<picture>`: wide/compact × light/dark, exported with
`film.mjs --still --pad 0 [--dark] [--width 390]` into `docs/assets/`. Because the README is the page, the
stage carries GitHub's page colors (#ffffff / #0d1117) and OG Doc's own `--dms-*` values, light and dark.
The playground defines no `--dms-*`, which first left the dark render on the light accent, with white
text on #60a5fa. It is never mounted in an app, so no host component exists.

**Gate** (CHROME_PATH = Chrome for Testing 153): all four captures end `rehearsal passed`.
- **Left in on purpose:** one informational `layout` line. The page "leaves 7px below its last child". The
  page bleeds off the workspace's foot, so its bottom padding is cropped on purpose. It is not a
  never-intentional line.
- **Found by the gate and fixed:**
  - the page floating with uneven gutters, now bleeding at the foot;
  - the tab tangent to the well, fixed with the preview head;
  - the items block reading as a container, flattened with a separate rule;
  - at 390, the wide design shrunk to 0.41, fixed by building the compact design.

**Review** (fresh-eye subagent, on the first passing version), and the response to each point:
1. **Window floated, dead right-hand corners.** The workspace now runs ~120px past the right edge.
2. **Bars where the eye lands, card over the only content.** The page got real lines (vendor, invoice
   number, total), and the card sits on the empty well beside the page.
3. **Blank pill in the preview head.** It is now the real version picker, "Version auswählen [Version 2]".
4. **Filename three times.** The head's title was dropped; the tab and the row remain.
5. **More placeholder rows than real ones, folder below files.** Three filler rows remain, and the folder
   comes first as in the product.
6. **Compact card ~80% inside.** The pane is narrower, so the card is ~30% outside. A bleed was rejected,
   because it would cut the upload button.
7. **Faint bars on white.** All bars went one tone darker.

Found afterwards in my own pass: the card over the page's amount column and, in compact, over the real
`Rechnung_März.pdf` row. The page is narrower and the compact card lower; the compact stage grew to 420.

**Not checked:**
- A second fresh-eye pass after the fixes.
- GitHub's actual rendering of the width `media` query. If GitHub or its app ignores it, the wide light
  image is the `<img>` fallback.
- The playground's own fonts are system-ui, because the playground has no `--dms-font-family`. The PNGs
  therefore use the capture machine's sans-serif, not necessarily the apps' font.

**Fix (P86-S2):** the `<picture>` check on GitHub at 390px (light and dark) confirmed that GitHub honors
the width query: the compact PNGs are served below 600px and the wide one at 1280. It also showed that
`og-doc-hero-compact-dark.png` carried the playground page's 16px side gutters in white (32px at 2×).
The two bands were recolored to GitHub's dark page color (#0d1117), after checking that they were pure
white and that the first column inside was already #0d1117. A re-film should give the film page the dark
background for `--dark --width 390`.
