# Rehearsal: reviewer-ui login illustration (P83-S3)

**Gold:** `form-error` (the button confirms on itself) and `dark-hero` (a status changes on its own
pill). The framing is the shared fade-crop family.

**Story** (plays once, about 3s, then holds; `poster` = the approved row):
1. The cursor reaches "Freigeben" on a request filed by alice, aimed below-right of the label so the
   arrow never covers the word.
2. Press.
3. The request's own pill turns from "Offen" to "Freigegeben".
4. The actions fold their width away, and the pill comes to rest at the row's end, in line with the pill
   slots of the requests still waiting.

The cursor idles out after 1.4s. Live check: `data-idle` at about 7s.

**Real strings:** the four tabs, `document.force_unlock` (the real, technical action code the list
shows), "Beantragt von alice", Offen/Freigegeben, Ablehnen/Freigeben. All labels come from
reviewer-ui's own i18n keys.

**Honest simplification:** the real app first asks "Diese Anfrage wirklich freigeben?" in a native
`confirm()`. That is a browser dialog, not product UI, so it is left out.

**Gate** (CHROME_PATH = Chrome for Testing 153, ADR 0237 Decision 5): `--still --reduced` in light and dark gives `film: no-issues-detected` for the stills, and `rehearsal passed` for the motion in both themes. No line is excused.

**Live** in the rebuilt app image at 1400:
- Light, dark, reduced motion and EN render. High contrast and 1199px show nothing.
- The pane top equals the card top (y=286).
- No page errors, and a real login afterwards works.

**Film:**
- `tight lines` on the placeholder rows → the filler lines keep the real row's 11px of air.
- `tight stack` tabs/heading → the page heading was dropped, because it only repeated the active tab
  ("Say it once").
- The cursor never idled out. A faded button keeps the kit's `data-hover`, so the actions are remounted
  as a folded twin once settled.
- Then a self-inflicted repeat: I anchored the cursor's rest point on the request row, and the row sat
  under the resting cursor, so it never idled. The rest point now hangs off the tab strip. The still
  first reported `inconclusive` because the rest-point target (`approve`) no longer existed in the end
  state; also fixed by the tab-strip anchor.

**Review** (fresh-eye subagent) and response:
1. The end state was the weakest frame, with the pill stranded mid-row → the actions now fold away and
   the pill settles at the row's end.
2. The pill was out of line with the skeleton pills → the skeleton rows have one pill slot at the row's
   end, aligned in DE and EN.
3. The payoff was the smallest mark, against button slabs → the slabs are gone, so the pill and the real
   row lead.
4. Inset dividers under a full-width tab rule → `.og-list--ruled` gives full-width dividers.
5. The arrow covered "ge" → aimed off the label. After the press the cursor fades (see above).

**Not checked:** the beats at 40–80ms (the press and the fold), since the contact sheet samples every
500ms; a second fresh-eye pass.

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
