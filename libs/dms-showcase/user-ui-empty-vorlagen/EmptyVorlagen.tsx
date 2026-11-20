import { Folder } from 'lucide-react'
import { Bar, Stage, type Theme } from '../kit'
import '../kit/stageshow.css'
import '../look-ogdoc.css'
import './empty-vorlagen.css'

// user-ui empty-state illustration for the Vorlagen pane (P83-S4, ADR 0237).
// A still, shown above the pane's own "Noch keine Vorlagen." line.
//
// What a structure template is, without words: a real folder subtree (solid
// folders, content bars), and in front of it the template captured from it -
// the *same shape*, row for row and indent for indent, but only as outlines:
// outlined folders and dashed bars. That is exactly what the product
// captures (names and object types, no attribute values). The one accent is
// spent on that captured structure, the point of the picture.
//
// Gray art and unframed for the same reasons as the empty preview
// (see EmptyPreview.tsx); every edge keeps a margin at least as deep as the
// shadows.

export const EMPTY_STAGE = { width: 320, height: 192 }

const TREE: { depth: number; w: number }[] = [
  { depth: 0, w: 72 },
  { depth: 1, w: 48 },
  { depth: 1, w: 56 },
  { depth: 1, w: 44 },
]

export default function EmptyVorlagenIllustration({ theme = 'light' }: { theme?: Theme }) {
  return (
    <Stage width={EMPTY_STAGE.width} height={EMPTY_STAGE.height} initial={{}} theme={theme} unframed className="og og-empty-vorlagen">
      {() => (
        <>
          {/* The real subtree: solid folders, content */}
          <div className="og-card og-tree">
            {TREE.map((n, i) => (
              <div key={i} className="og-tree__row" data-depth={n.depth}>
                <Folder size={14} strokeWidth={1.5} fill="currentColor" className="og-tree__icon" />
                <Bar w={n.w} h={6} tone={n.depth === 0 ? 3 : 2} />
              </div>
            ))}
          </div>

          {/* The template: the same shape, outlines only */}
          <div className="og-card og-template">
            {TREE.map((n, i) => (
              <div key={i} className="og-tree__row" data-depth={n.depth}>
                <Folder size={14} strokeWidth={2} className="og-template__icon" />
                <span className="og-template__bar" style={{ width: n.w }} />
              </div>
            ))}
          </div>
        </>
      )}
    </Stage>
  )
}
