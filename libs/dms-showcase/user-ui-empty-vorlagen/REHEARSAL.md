# Rehearsal: user-ui empty Vorlagen pane illustration (P83-S4)

**Gold:** `stacked-cards` (two components, the front one over the back one's corner). Unframed, for
the same reason as the empty preview.

**Story** (still, wordless gray art): a real folder subtree (filled gray folders, content bars), and in
front of it the template captured from it. The template is the *same shape*, row for row and indent for
indent, only as outlines: outlined folders and dashed bars, in the one accent. That is exactly what the
product captures, structure without content. It sits above "Noch keine Vorlagen.".

**Review** (fresh-eye subagent) and response:
1. The payoff (the dashed "structure only" outline) was the faintest ink, and the accent was spent on a
   generic layout glyph → the accent moved onto the captured structure itself, and the `LayoutTemplate`
   glyph is gone.
2. Shadow cut at the bottom → stage 192 tall; the template ends at y 168.
3. The captured shape did not match the tree → identical rows, pitch (24px) and indent; the template
   overlaps the tree's corner by only 16px, so the tree's shape stays visible.
4. In dark the front card had the back card's surface → it comes forward one step lighter.
5. Near-tangents (a bar 3px from the card, a tile 1px off the tree's edge) → the tree's bars end 14px
   clear of the template, and the tile is gone.
6. `LayoutTemplate` read as "page layout" → removed (see 1).
7. The art floated between the form and the sentence → see the set-level responses (the gap to the
   sentence is now 8px plus the shadow margin).

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
