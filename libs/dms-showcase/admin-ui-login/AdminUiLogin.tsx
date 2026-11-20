import { ChevronRight, File } from 'lucide-react'
import { Bar, Connector, Field, Stage, type Theme } from '../kit'
import '../kit/stageshow.css'
import '../look-ogdoc.css'
import './admin-ui-login.css'

// admin-ui login illustration (P83-S3, ADR 0237). A still.
//
// Panel over product, read from the admin's side: the object-type editor for
// the document class "Eingangsrechnung", and the one setting the story is
// about - its Kennzeichen format - with one leader to its effect: a document
// row as end users see it in user-ui, "2026-114 Rechnung_Mai.pdf" (the same
// document the user-ui login illustration files). Setting and effect are both
// real: object-type-service renders kennzeichen_format into the number, and
// user-ui shows it before the title (lib/kennzeichen.ts).
//
// admin-ui has no tab strip: the pane opens on a breadcrumb bar (page, then
// the class being edited), which also names the class, so no Name field.
//
// Real words: the breadcrumb, the format's label and value, the folder and
// the document row. The rest of the editor is filler.

export type AdminUiLoginStrings = {
  page: string
  name: string
  formatLabel: string
  folder: string
  file: string
}

const DE: AdminUiLoginStrings = {
  page: 'Objekttypen',
  name: 'Eingangsrechnung',
  formatLabel: 'Kennzeichen-Format',
  folder: 'Eingangsrechnungen',
  file: 'Rechnung_Mai.pdf',
}

// The format string is configuration syntax, the same in every language
const FORMAT = '{YYYY}-{Laufende_Nummer}'
const KENNZEICHEN = '2026-114'
// The format field's box is the lower 34px of its target (label above it)
const BOX = 34
// Card top to its row's vertical center: 16px padding, the folder label's
// line (12px * 1.5) and 4px gap, then half the 40px row
const ROW_CENTER = 16 + 18 + 4 + 20

export default function AdminUiLoginIllustration({ theme = 'light', strings = DE }: { theme?: Theme; strings?: AdminUiLoginStrings }) {
  const t = strings
  return (
    <Stage width={720} height={400} initial={{}} theme={theme} className="og og-admin">
      {(_s, rect) => {
        const field = rect('format')
        const row = rect('effect')
        // The leader runs level from the format's box to the row: one y for
        // both ends, measured from the field, never hard-coded
        const y = field.height ? field.y + field.height - BOX / 2 : 0
        return (
          <>
            <div className="og-mask" data-satellite>
              <div className="og-pane">
                <div className="og-bar og-bar--head">
                  <span className="og-crumbs">
                    <span className="og-crumb">{t.page}</span>
                    <ChevronRight size={14} strokeWidth={2} className="og-crumb-sep" />
                    <span className="og-heading">{t.name}</span>
                  </span>
                </div>
                <div className="og-form">
                  <div data-target="format">
                    <Field label={t.formatLabel} value={FORMAT} className="og-format" />
                  </div>
                  {/* "Gilt für", display override, retention, attributes:
                      filler as empty, outlined fields - light, so the one
                      real value stays the heaviest mark */}
                  {/* Enough of them to run well past the stage's foot */}
                  {[64, 120, 96, 112, 88].map((w) => (
                    <div className="og-filler" key={w}>
                      <Bar w={w} h={7} tone={2} />
                      <span className="og-blank" />
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* The effect, as user-ui shows it. The satellite straddles the
                pane's right edge, its row level with the setting */}
            <div className="og-card og-effect" style={{ top: y ? y - ROW_CENTER : 120 }}>
              <div className="og-effect__folder">{t.folder}</div>
              <div className="og-row" data-target="effect">
                <span className="og-name">
                  <File size={16} strokeWidth={1.5} className="og-icon" data-focus />
                  <span className="og-kz">{KENNZEICHEN}</span>
                  <span className="og-name__text">{t.file}</span>
                </span>
              </div>
            </div>

            <Connector
              show={Boolean(y)}
              from={{ x: field.x + field.width + 8, y }}
              to={{ x: row.x - 4, y }}
              route="straight"
              dots="both"
              className="og-leader"
            />
          </>
        )
      }}
    </Stage>
  )
}
