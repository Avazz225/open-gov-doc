import { File } from 'lucide-react'
import { Bar, Stage, type Theme } from '../kit'
import '../kit/stageshow.css'
import '../look-ogdoc.css'
import './empty-preview.css'

// user-ui empty-state illustration for the preview pane (P83-S4, ADR 0237).
// A still, shown above the pane's own "Kein Dokument ausgewählt." line.
//
// Gray art (craft.md: the one place it belongs is an empty state) and
// wordless on purpose: inside a functional pane, a real filename would read
// as if a document were actually open. The pane's own sentence says it in
// words; the picture shows the cause and effect - a row selected in the
// explorer, the document page it opens - with the one accent on the
// selection.
//
// Unframed: an empty-state spot sits whole and centered in a pane whose width
// the user changes (dockview), where a crop would read as a rendering fault.
// framing.md's condition for unframed holds: every edge keeps a margin at
// least as deep as the shadows (the stage is 192 tall for that).

export const EMPTY_STAGE = { width: 320, height: 192 }

const LINES = [100, 86, 94, 72, 90, 64]
const ROWS = [64, 82, 56]

export default function EmptyPreviewIllustration({ theme = 'light' }: { theme?: Theme }) {
  return (
    <Stage width={EMPTY_STAGE.width} height={EMPTY_STAGE.height} initial={{}} theme={theme} unframed className="og og-empty-preview">
      {() => (
        <>
          {/* The document page the selection opens: the hero */}
          <div className="og-page">
            <Bar w={58} h={8} tone={3} />
            <div className="og-page__lines">
              {LINES.map((w, i) => (
                <Bar key={i} w={w} h={5} tone={2} />
              ))}
            </div>
          </div>

          {/* The explorer list, straddling the page's left edge, its top clear
              of the page's title bar */}
          <div className="og-card og-list-card">
            {ROWS.map((w, i) => (
              <div key={i} className="og-mini-row" data-selected={i === 1 || undefined}>
                {/* Small icons are filled (craft.md): a solid page glyph */}
                <File size={14} strokeWidth={1.5} fill="currentColor" className="og-mini-icon" />
                <Bar w={w} h={6} tone={i === 1 ? 4 : 2} />
              </div>
            ))}
          </div>
        </>
      )}
    </Stage>
  )
}
