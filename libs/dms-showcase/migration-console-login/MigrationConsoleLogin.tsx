import { Badge, Bar, Check, Connector, Stage, type Theme } from '../kit'
import '../kit/stageshow.css'
import '../look-ogdoc.css'
import './migration-console-login.css'

// migration-console login illustration (P83-S3, ADR 0237). A still.
//
// The transfer list with one finished transfer to a paired installation -
// "2/2 kopiert, 2 verifiziert", released - and its "Zeitverlauf" (the
// transfer detail's phase timeline) as a satellite straddling the pane's
// edge below the row it belongs to: created, locked, copied, verified,
// released, with the source's scheduled deletion still ahead. All of it is
// real: the progress line and the phase names are migration-console's own
// strings, the phases migration-service's transfer workflow.
//
// Honest simplification: the real list shows the status as its raw enum
// today (P83-S1 punch list, item 6); the pill here uses the phase's German
// label, which the list will show once that item is fixed.
//
// Real words: the tabs, the transfer's id, target, progress and status, the
// timeline's heading and phases. The neighbour rows are filler.

export type MigrationConsoleLoginStrings = {
  tabs: [string, string]
  target: string
  progress: string
  status: string
  timeline: string
  phases: string[]
  pendingPhase: string
}

const DE: MigrationConsoleLoginStrings = {
  tabs: ['Transfers', 'Gepaarte Installationen'],
  target: 'Landesarchiv',
  progress: '2/2 kopiert, 2 verifiziert',
  status: 'Freigegeben',
  timeline: 'Zeitverlauf',
  phases: ['Angelegt', 'Gesperrt', 'Kopiert', 'Verifiziert', 'Freigegeben'],
  pendingPhase: 'Löschung geplant',
}

// A transfer id is an identifier, the same in every language
const ID = '3af7c7d7'
// Gap between the status pill and the timeline card it details
const LEADER = 24
const FILL = [
  [64, 104, 132],
  [64, 88, 148],
  [64, 112, 120],
  [64, 96, 140],
]

export default function MigrationConsoleLoginIllustration({ theme = 'light', strings = DE }: { theme?: Theme; strings?: MigrationConsoleLoginStrings }) {
  const t = strings
  return (
    <Stage width={720} height={400} initial={{}} theme={theme} className="og og-migration">
      {(_s, rect) => {
        const pill = rect('status')
        // The timeline hangs a gutter below the row's status pill it details,
        // tied to it by one short leader (measured, never hard-coded)
        const top = pill.height ? pill.y + pill.height + LEADER : 140
        return (
        <>
          <div className="og-mask" data-satellite>
            <div className="og-pane">
              <div className="og-tabs">
                {t.tabs.map((tab, i) => (
                  <span key={tab} className="og-tab" data-on={i === 0 || undefined}>
                    {tab}
                  </span>
                ))}
              </div>
              <div className="og-list og-list--ruled">
                <div className="og-row og-transfer">
                  <span className="og-transfer__what">
                    <span className="og-code">{ID}</span>
                    <span className="og-meta">{t.target}</span>
                  </span>
                  <span className="og-transfer__state">
                    <span className="og-meta">{t.progress}</span>
                    <span data-target="status">
                      <Badge tone="success">{t.status}</Badge>
                    </span>
                  </span>
                </div>
                {FILL.map(([a, b, c], i) => (
                  <div className="og-row" key={i}>
                    <span className="og-transfer__what og-transfer__what--fill">
                      <Bar w={a} h={7} tone={3} />
                      <Bar w={b} h={7} tone={2} />
                    </span>
                    {/* Progress only: no pill slot, whose sliver would peek
                        out beside the timeline card */}
                    <Bar w={c} h={7} tone={2} />
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* The transfer's detail: its phase timeline, straddling the
              pane's edge just below the row it belongs to */}
          <Connector show={Boolean(pill.height)} from={{ x: pill.x + pill.width / 2, y: pill.y + pill.height + 4 }} to={{ x: pill.x + pill.width / 2, y: top - 1 }} route="straight" dots="none" className="og-leader" />
          <div className="og-card og-timeline" style={{ top }}>
            <div className="og-timeline__heading">{t.timeline}</div>
            <ol className="og-timeline__phases">
              {/* "Freigegeben" is said once, by the row's pill; here it is
                  just the latest phase reached */}
              {t.phases.map((phase) => (
                <li key={phase} className="og-phase">
                  <Check on size={14} className="og-phase__mark" />
                  <span>{phase}</span>
                </li>
              ))}
              {/* Still ahead: the kit's open ring (not "pending", which
                  draws a spinner - nothing is running yet) */}
              <li className="og-phase" data-ahead>
                <Check on={false} size={14} className="og-phase__mark" />
                <span>{t.pendingPhase}</span>
              </li>
            </ol>
          </div>
        </>
        )
      }}
    </Stage>
  )
}
