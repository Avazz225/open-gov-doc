# Rehearsal: admin-ui login illustration (P83-S3)

**Gold:** `panel-annotation` (a setting with one leader to what it changed). The framing is the
fade-crop family shared by all five login illustrations: the pane on the page, no plate, its top level
with the login card's.

**Story** (still): the object-type editor for "Eingangsrechnung" shows its Kennzeichen format
`{YYYY}-{Laufende_Nummer}`. One level, neutral leader runs from that field to its effect, the row
`2026-114 Rechnung_Mai.pdf` as user-ui shows it (the same document the user-ui illustration files).

**Real strings:** Objekttypen, Eingangsrechnung, Kennzeichen-Format, the format, Eingangsrechnungen,
2026-114, Rechnung_Mai.pdf.

**Changed during the build:**
- The ADR's "retention setting with a leader to its folder" became the Kennzeichen format, because the
  product shows no per-folder retention link.
- The tab strip became a breadcrumb head: admin-ui has no tabs, which also removed a stubborn
  `tight stack` line.
- The Name field was dropped (the breadcrumb names the class).
- The format field moved up out of the fade.
- The leader was made level by measuring one y for both ends.

**Gate** (CHROME_PATH = Chrome for Testing 153, ADR 0237 Decision 5): `--still --reduced` in light and dark gives `film: no-issues-detected`. No line is excused.

**Live** in the rebuilt app image at 1400:
- Light, dark, reduced motion and EN render. High contrast and 1199px show nothing.
- The pane top equals the card top (y=286).
- No page errors, and a real login afterwards works.

**Film:**
- `uneven padding` on the card → padding 16/8 plus the row's 8.
- `tight stack` tabs/heading → the tabs were removed (see above).
- `og-blank ends 2px short of the bottom edge`, then `runs only 14px past` → the field gap is 22px, so the
  last whole field ends about 45px inside and the next runs about 30px past.
- Found by eye, not by the gate: after the fields went full width, the leader ran *through*
  "2026-114". The measured target was the full-width row, not the field. Fixed: the target wrapper is
  300px wide.

**Review** (fresh-eye subagent) and response:
1. Half the pane was empty → the filler fields span the pane and run under the satellite.
2. The skeleton slabs were the heaviest marks → they are now light outlined empty fields.
3. White dot rims in dark, accent-blue leader → the rims use the surface token and the leader is neutral
   ink (`.og-leader`, in look-ogdoc).
4. The satellite was aligned to nothing → its row is level with the field (measured). That is the
   anchor.
5. The bold mono value outweighed its result → now weight 500.
6. EN shows `{YYYY}-{Laufende_Nummer}` → **left as is**: it is configuration syntax an installation
   defines, not UI copy, and it is the same in any UI language.
7. No rule under the breadcrumb → `.og-bar--head` gives admin and designer the same rule.

**Not checked:** a second fresh-eye pass after these fixes; Firefox/Safari rendering.

**Set-level review responses** (shared with the other three P83-S3 illustrations):
- **Pane width.** It was 496/511/527 wide in the satellite scenes. One `data-satellite` column (184px)
  now gives every satellite scene the same pane width.
- **Satellite placement.** Each satellite is anchored to its subject: admin's row level with its field,
  designer's panel level with the selection, migration's card under its pill by a leader.
- **Dark mode.** Satellites take a lighter raised surface (`--og-card-bg`) instead of shadow alone.
- **Breadcrumb heads** (admin, designer) carry the same rule.
- **Ruled lists** (reviewer, migration) have full-width dividers.
- **Left as is:** reviewer and migration both end on a green "Freigegeben". It is the real word for the
  real outcome in both products, so changing one would make it less true, not more varied.
