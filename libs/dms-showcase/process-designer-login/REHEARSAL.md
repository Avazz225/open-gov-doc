# Rehearsal: process-designer login illustration (P83-S3)

**Gold:** `annotations` / `panel-annotation` for the panel. The framing is the shared fade-crop family.
The properties panel straddles the pane's edge because the real designer docks it at the canvas's right.

**Story** (still): an invoice process, start → "Rechnung prüfen" → "Freigabe zeichnen" → end, with the
signature task selected (bpmn-js's own thin selection outline). Its panel shows "Signatur",
"Signatur erforderlich" on and "Mindestniveau" AES, as `SignatureTaskPropertiesProvider` sets it. A faint
second branch runs on into the fade.

**Real strings:** Prozessdefinitionen, Rechnungsfreigabe, Als XML exportieren, the two task names,
Signatur, Signatur erforderlich, Mindestniveau, AES.

**Honest simplification:** bpmn-js renders the panel's own group chrome (Preact, English). The panel
here shows its content in OG Doc's words. The group label "Signatur" now comes from the real
`signatureTask.groupLabel` (P83-S5 stripped its "(3.10)"; until then it had its own key).

**Gate** (CHROME_PATH = Chrome for Testing 153, ADR 0237 Decision 5): `--still --reduced` in light and dark gives `film: no-issues-detected`. No line is excused.

**Live** in the rebuilt app image at 1400:
- Light, dark, reduced motion and EN render. High contrast and 1199px show nothing.
- The pane top equals the card top (y=286).
- No page errors, and a real login afterwards works.

**Film:**
- Gate lines: none.
- By eye: the end event sat about 4px from the panel → the flows are 28px.
- Floating gray filler blocks → replaced by a faint outlined branch.

**Review** (fresh-eye subagent) and response:
1. About 60% of the canvas was empty → a faint second branch (an outlined flow off the review step)
   runs into the fade.
2. The black 2px borders and a double ring outweighed the story → the shapes use text ink at 1.5px and
   the selection is one 1.5px line at a 6px offset, as bpmn-js draws it.
3. The arrows run through the selection ring → **kept**: bpmn-js's flows meet the shape, not its
   outline, so the real modeler crosses it too.
4. Thin, faint task markers → stroke 2.25 at the icon ink, set clear of the name.
5. A faint panel chevron, an unanchored panel top, a bold AES → icon-ink chevron, the panel top level
   with the selection outline (measured), and the value at normal weight.
6. EN task names wrap differently → **kept**: the modeler wraps by width too.

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
