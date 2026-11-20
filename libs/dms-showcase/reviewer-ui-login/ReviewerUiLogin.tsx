import { Badge, Bar, Button, Stage, States, type Act, type Theme } from '../kit'
import '../kit/stageshow.css'
import '../look-ogdoc.css'
import './reviewer-ui-login.css'

// reviewer-ui login illustration (P83-S3, ADR 0237). Plays once, then holds.
//
// The four-eyes inbox: one open request, filed by someone else ("alice"). The
// reviewer presses "Freigeben"; the request's own status pill turns from
// "Offen" to "Freigegeben" (the status changes on its own pill, as the
// dark-hero gold does) and the two actions fade out where they stood, so the
// row keeps its geometry. Payoff and poster: the approved row.
//
// Honest simplifications: the real app asks "Diese Anfrage wirklich
// freigeben?" in a native confirm() dialog first - left out, a browser dialog
// is not product UI. The action type is the real, technical code the list
// shows (an operation the four-eyes rule guards), in monospace.
//
// Real words: the tabs, the request's code, its requester, the
// pill and the two buttons. The neighbour rows are filler.

type S = { approved: boolean; settled: boolean }
const initial: S = { approved: false, settled: false }
const poster: S = { approved: true, settled: true }

export type ReviewerUiLoginStrings = {
  tabs: [string, string, string, string]
  requestedBy: string
  pending: string
  approved: string
  approve: string
  reject: string
}

const DE: ReviewerUiLoginStrings = {
  tabs: ['Aufgaben', 'Freigaben', 'Team', 'Vorgänge'],
  requestedBy: 'Beantragt von alice',
  pending: 'Offen',
  approved: 'Freigegeben',
  approve: 'Freigeben',
  reject: 'Ablehnen',
}

const ACTION = 'document.force_unlock'
const FILL = [
  [168, 96],
  [148, 112],
  [180, 88],
  [132, 104],
]

async function script(a: Act<S>) {
  await a.wait(700)
  // 1. The reviewer goes to the request's approve button
  a.mark('reach')
  // Aimed below-right of the label, so the arrow never covers the word
  await a.move({ at: 'approve', x: 0.8, y: 0.72 })
  await a.wait(300)
  // 2. Press
  a.mark('press')
  await a.click()
  // 3. The request is approved: its pill turns on itself, then the actions
  //    fold away and the pill comes to rest at the row's end, in line with
  //    the pills of the requests still waiting below
  a.mark('approved')
  await a.set({ approved: true })
  // After the fold (0.15s delay + 0.45s) the actions settle as a twin
  await a.wait(700)
  await a.set({ settled: true })
  await a.wait(2000)
}

export default function ReviewerUiLoginIllustration({ theme = 'light', strings = DE }: { theme?: Theme; strings?: ReviewerUiLoginStrings }) {
  const t = strings
  return (
    <Stage<S>
      width={720}
      height={400}
      initial={initial}
      poster={poster}
      play="once"
      theme={theme}
      script={script}
      // The rest point hangs off the tab strip: a target that exists in every
      // state and never sits under the cursor at the end (a target under a
      // resting cursor counts as hovered, and a hovered cursor never idles out)
      cursor={{ at: { at: 'tabs', x: 0.82, y: 5 }, idle: 1400 }}
      className="og og-reviewer"
    >
      {(s) => (
        <div className="og-mask">
          <div className="og-pane">
            <div className="og-tabs" data-target="tabs">
              {t.tabs.map((tab, i) => (
                <span key={tab} className="og-tab" data-on={i === 1 || undefined}>
                  {tab}
                </span>
              ))}
            </div>
            {/* No page heading: "Vier-Augen-Freigaben" would only repeat the
                active "Freigaben" tab (craft.md, "Say it once") */}
            <div className="og-list og-list--ruled">
              <div className="og-row og-request" data-approved={s.approved || undefined}>
                <span className="og-request__what">
                  <span className="og-code">{ACTION}</span>
                  <span className="og-meta">{t.requestedBy}</span>
                </span>
                <span className="og-request__state">
                  <States
                    value={s.approved ? 'approved' : 'pending'}
                    align="end"
                    states={{
                      pending: <Badge tone="warning">{t.pending}</Badge>,
                      approved: <Badge tone="success">{t.approved}</Badge>,
                    }}
                  />
                  {/* The actions fold their own width away (grid 1fr -> 0fr)
                      so the pill slides to the row's end. Once folded
                      (settled) they are remounted as a folded twin: the old
                      button would keep the kit's data-hover mark, and a
                      cursor resting on something hovered never idles out */}
                  <span
                    key={s.settled ? 'settled' : 'live'}
                    className="og-request__fold"
                    data-settled={s.settled || undefined}
                    aria-hidden={s.settled || undefined}
                  >
                    <span className="og-request__actions">
                      <Button>{t.reject}</Button>
                      <Button
                        variant="primary"
                        data-target={s.settled ? undefined : 'approve'}
                        data-cursor={s.settled ? undefined : 'pointer'}
                      >
                        {t.approve}
                      </Button>
                    </span>
                  </span>
                </span>
              </div>
              {FILL.map(([a, b], i) => (
                <div className="og-row" key={i}>
                  <span className="og-request__what og-request__what--fill">
                    <Bar w={a} h={7} tone={3} />
                    <Bar w={b} h={7} tone={2} />
                  </span>
                  {/* Waiting requests: a pill slot only, at the row's end
                      where the approved pill comes to rest - no button slabs
                      outweighing the story */}
                  <Bar w={84} h={20} r={10} tone={1} />
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </Stage>
  )
}
