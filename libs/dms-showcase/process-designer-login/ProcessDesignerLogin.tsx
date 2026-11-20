import { ChevronDown, ChevronRight, PenLine, User } from 'lucide-react'
import { Button, Connector, Field, Stage, Toggle, type Theme } from '../kit'
import '../kit/stageshow.css'
import '../look-ogdoc.css'
import './process-designer-login.css'

// process-designer login illustration (P83-S3, ADR 0237). A still.
//
// The BPMN designer with a short invoice process: start, "Rechnung prüfen",
// "Freigabe zeichnen", end. The signature task is selected (bpmn-js draws a
// selection outline - real product chrome, not an invented highlight), and
// its properties panel, docked to the canvas's right in the real designer,
// straddles the pane's edge with the one setting the story is about: a
// signature is required, at minimum level AES. Both are real: the designer's
// own "Signatur" property group (SignatureTaskPropertiesProvider) writes
// taskType=signature / requiredLevel, and workflow-service enforces it.
//
// Honest simplification: bpmn-js renders the panel's own group chrome
// (English, Preact); this shows its content only, in OG Doc's words.
//
// Real words: the breadcrumb, the two task names, the group, the toggle's
// label, the level's label and value, the export button.

export type ProcessDesignerLoginStrings = {
  list: string
  process: string
  export: string
  review: string
  sign: string
  group: string
  required: string
  levelLabel: string
}

const DE: ProcessDesignerLoginStrings = {
  list: 'Prozessdefinitionen',
  process: 'Rechnungsfreigabe',
  export: 'Als XML exportieren',
  review: 'Rechnung prüfen',
  sign: 'Freigabe zeichnen',
  group: 'Signatur',
  required: 'Signatur erforderlich',
  levelLabel: 'Mindestniveau',
}

// A signature level is an eIDAS term, the same in every language
const LEVEL = 'AES'
// The selection outline's offset from the task (see the CSS)
const SELECTION_OFFSET = 6

export default function ProcessDesignerLoginIllustration({ theme = 'light', strings = DE }: { theme?: Theme; strings?: ProcessDesignerLoginStrings }) {
  const t = strings
  return (
    <Stage width={720} height={400} initial={{}} theme={theme} className="og og-designer">
      {(_s, rect) => {
        const start = rect('start')
        const review = rect('review')
        const sign = rect('sign')
        const end = rect('end')
        const branch = rect('branch')
        const next = rect('next')
        const ready = Boolean(start.width && end.width && branch.width)
        const mid = (r: { y: number; height: number }) => r.y + r.height / 2
        return (
          <>
            <div className="og-mask" data-satellite>
              <div className="og-pane">
                <div className="og-bar og-bar--head">
                  <span className="og-crumbs">
                    <span className="og-crumb">{t.list}</span>
                    <ChevronRight size={14} strokeWidth={2} className="og-crumb-sep" />
                    <span className="og-heading">{t.process}</span>
                  </span>
                  <Button>{t.export}</Button>
                </div>
                <div className="og-canvas">
                  <span className="og-event" data-target="start" />
                  <span className="og-task" data-target="review">
                    <User size={14} strokeWidth={2.25} className="og-task__marker" />
                    <span className="og-task__name">{t.review}</span>
                  </span>
                  <span className="og-task" data-target="sign" data-selected>
                    <PenLine size={14} strokeWidth={2.25} className="og-task__marker" />
                    <span className="og-task__name">{t.sign}</span>
                  </span>
                  <span className="og-event og-event--end" data-target="end" />
                  {/* The rest of the process: a faint second branch off the
                      review step, running on into the fade - filler, so the
                      canvas reads as a model that continues, not a void */}
                  <span className="og-task og-task--faint" data-target="branch" />
                  <span className="og-task og-task--faint" data-target="next" />
                </div>
              </div>
            </div>

            {/* Sequence flows, measured between the shapes */}
            {ready ? (
              <>
                <Connector show from={{ x: start.x + start.width, y: mid(start) }} to={{ x: review.x - 1, y: mid(start) }} route="straight" arrow className="og-flow" />
                <Connector show from={{ x: review.x + review.width, y: mid(review) }} to={{ x: sign.x - 1, y: mid(review) }} route="straight" arrow className="og-flow" />
                <Connector show from={{ x: sign.x + sign.width, y: mid(sign) }} to={{ x: end.x - 1, y: mid(sign) }} route="straight" arrow className="og-flow" />
                <Connector show from={{ x: review.x + review.width / 2, y: review.y + review.height }} to={{ x: review.x + review.width / 2, y: branch.y - 1 }} route="straight" arrow className="og-flow og-flow--faint" />
                <Connector show from={{ x: branch.x + branch.width, y: mid(branch) }} to={{ x: next.x - 1, y: mid(branch) }} route="straight" arrow className="og-flow og-flow--faint" />
              </>
            ) : null}

            {/* The properties panel: docked right in the real designer */}
            {/* Its top is level with the selected task's outline (measured) */}
            <div className="og-card og-props" style={{ top: sign.height ? sign.y - SELECTION_OFFSET : 100 }}>
              <div className="og-props__group">
                <ChevronDown size={14} strokeWidth={2.25} className="og-props__chevron" />
                <span>{t.group}</span>
              </div>
              <div className="og-props__toggle">
                <Toggle on />
                <span>{t.required}</span>
              </div>
              <Field
                label={t.levelLabel}
                value={LEVEL}
                trailing={<ChevronDown size={14} strokeWidth={2} />}
                className="og-props__level"
              />
            </div>
          </>
        )
      }}
    </Stage>
  )
}
