# Rehearsal: user-ui empty preview pane illustration (P83-S4)

**Gold:** `static-card` (quiet hierarchy, one satellite). Unframed: an empty-state spot in a
user-resizable dockview pane, where a crop reads as a rendering fault (ADR 0237 records the call).

**Story** (still, wordless gray art): a row selected in the explorer list, the one accent, and the
document page it opens. It sits above the pane's own "Kein Dokument ausgewählt.". Wordless because inside a
functional pane a real filename would read as if a document were open.

**Review** (fresh-eye subagent) and response:
1. Shadow cut at the bottom → stage 192 tall; the page ends at y 164.
2. In dark the page was a hole (pane color) → a raised sheet in dark, and the list card in front one step
   lighter still.
3. Card top on the page's title bar (near-tangent) → the card top is at y 58, well below the bar
   (32..40).
4. Soft at 326px panes (0.887) → the gate is now ≥ 304px of content (≥ 0.95).
5. Wiry outline file icons → filled page glyphs (craft.md: small icons are filled).
6. Loose to its sentence → see the set-level responses.

**Left as is:** "Nothing ties the row to the page except the overlap." The selection is the one accent
and the page sits right behind it. Adding a connector would explain more than an empty state should.

**Gate** (CHROME_PATH = Chrome for Testing 153): `--still` in light and dark gives `film: no-issues-detected`.
No line is excused.

**Live** in the rebuilt user-ui image, logged in as users-admin:
- At 1400 light and dark the picture shows above the unchanged sentence.
- At 900 the preview pane is 426px wide and shows it.
- At 700 (pane 326, content ~286) and 600 (pane 276) the preview pane shows no picture: content width
  < 304, by design. The Vorlagen pane is wider and keeps its picture.
- No page errors.

**Host rules** (`apps/user-ui/src/components/EmptyStateIllustration.tsx`, ADR 0237 Decision 4):
- Gated by the content width inside the pane: ≥ 304px, so the 320px stage renders at ≥ 0.95 scale.
- Nothing in high contrast.
- `aria-hidden`, because the sentence carries the meaning.
- The box is reserved while the module loads.

**Set-level review responses** (shared with the other empty-state still):
- **Shadows cut at the stage's foot.** The stage is now 320×192, and every edge keeps a margin at least as
  deep as the shadows (framing.md's condition for unframed).
- **Soft at narrow panes.** My first threshold measured 280px of *pane* width, about 0.75 scale. It is
  now 304px of *content* width (≥ 0.95), and the host comment, which had the maths wrong, is corrected.
- **Loose spacing to the sentence.** The host gap is 8px, so the sentence's ink sits about 32px under the
  art.
- **Stacked preview/metadata empty states, one centered and one left.** The metadata pane now uses the
  same centered `<EmptyState>` without a picture.
- **Possible layout jump while loading.** The picture's box is reserved by aspect ratio.
- **The ~7px offset from the pane's centre, art and sentence agreeing.** Not changed: it comes from the
  host pane's own right padding, not from the art.

**Not checked:**
- A second fresh-eye pass after the fixes.
- Retina rendering; all captures are 1×.
- High contrast live: users-admin's server-side theme preference overrides the local cache, so it was
  covered by `tests/empty-state-illustration.test.tsx` instead.
