# Rehearsal: migration-console login illustration (P83-S3)

**Gold:** `loading` (the row) and `annotations` (a leader). The framing is the shared fade-crop family.

**Story** (still): the transfer list with one finished transfer, `3af7c7d7` → Landesarchiv, "2/2 kopiert,
2 verifiziert", "Freigegeben". Its "Zeitverlauf" card hangs under the status pill by a short leader: five
reached phases (the kit's filled `Check`) on one line, and "Löschung geplant" still ahead (the kit's open
ring; not `pending`, which draws a spinner, and nothing is running yet).

**Real strings:** the two tabs, the id, the target, the progress line (`transfers.documentsProgress`,
interpolated), the status, the timeline heading and phases. All except the target come from
migration-console's own keys.

**Honest simplification:** the real list still shows the status as its raw enum (P83-S1 punch list
item 6). The pill uses the phase's German label, which the list will show once that item is fixed.

**Gate** (CHROME_PATH = Chrome for Testing 153, ADR 0237 Decision 5): `--still --reduced` in light and dark gives `film: no-issues-detected`. No line is excused.

**Live** in the rebuilt app image at 1400:
- Light, dark, reduced motion and EN render. High contrast and 1199px show nothing.
- The pane top equals the card top (y=286).
- No page errors, and a real login afterwards works.

**Film:**
- Gate lines: none.
- By eye: the card nearly touched the pill → it now hangs 24px below on a measured leader.
- "Freigegeben" said twice with emphasis → the bold was removed from the timeline.

**Review** (fresh-eye subagent) and response:
1. The popover was unanchored, 2px under the row's rule → measured: 24px under the pill, tied by a
   neutral leader.
2. Skeleton pill slivers at the card's edge → the skeleton rows have no pill slot.
3. A checklist, not a timeline, with no marker ahead → one line through the marks, filled checks for
   reached phases and an open ring ahead, punched out on the card's own surface (`--og-card-bg`, also
   right in dark).
4. Upside-down hierarchy → the id is 13px mono weight 500 and the meta 12.5/13px.
5. One real row against four skeletons → **kept**: the story is one transfer, and the timeline card is
   its second real object.

**Not checked:** a second fresh-eye pass; Firefox/Safari.

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
