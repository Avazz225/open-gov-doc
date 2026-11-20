import { useLayoutEffect, useRef, type ReactNode } from 'react'
import { ChevronRight, File, Folder, Upload } from 'lucide-react'
import { Bar, Button, Collapse, Meter, Stage, States, type Act, type Point, type Theme } from '../kit'
import '../kit/stageshow.css'
import './user-ui-login.css'

// user-ui login illustration (P83-S2, ADR 0237). Developed in the stageshow
// playground as `examples/ogdoc-login`; see REHEARSAL.md beside this file.
//
// OG Doc's own Explorer pane (not an OS window: OG Doc is a web app, so the
// pane carries the product's chrome - its tab, breadcrumb and upload button -
// in the same material as the login card beside it). Fade crop: the pane sits
// on the page itself, whole at the top and sides, and dissolves at its foot.
//
// A PDF is carried in from past the stage's left edge and dropped at the top
// of the list, where the upload row then opens. Its bar fills, the row
// settles, and the reference number the server assigns on creation
// ("Kennzeichen") grows in before the filename, in plain text the way the
// product shows it (lib/kennzeichen.ts: "<Kennzeichen> <title>"). The two real
// rows below already carry theirs (2026-113, 2026-112), so the new one reads
// as joining the sequence. That is the payoff; it stays (play once, poster).

type Ghost = { on: boolean; dropped: boolean; at: Point }
type S = { up: boolean; progress: number; done: boolean; kz: boolean; ghost: Ghost }

const initial: S = { up: false, progress: 0, done: false, kz: false, ghost: { on: false, dropped: false, at: { x: -80, y: 200 } } }
const poster: S = { up: true, progress: 1, done: true, kz: true, ghost: initial.ghost }

export type OgDocLoginStrings = {
  pane: string
  root: string
  folder: string
  upload: string
  file: string
  kennzeichen: string
  neighbors: [kennzeichen: string, file: string][]
}

const DE: OgDocLoginStrings = {
  pane: 'Explorer',
  root: 'Start',
  folder: 'Eingangsrechnungen',
  upload: 'Hochladen',
  file: 'Rechnung_Mai.pdf',
  kennzeichen: '2026-114',
  neighbors: [
    ['2026-113', 'Rechnung_April.pdf'],
    ['2026-112', 'Rechnung_März.pdf'],
  ],
}

// Filler rows below the real ones, carrying on into the fade
const FILL = [132, 104, 120, 96]

async function script(a: Act<S>) {
  // 1. The file arrives from the desktop, already carried, from past the left edge
  a.place({ x: -60, y: 120 })
  await a.wait(500)
  a.mark('carry')
  const list = a.rect('drop')
  // Aimed at the top of the list, where the upload row will open
  await a.drag({ x: list.x + list.width * 0.3, y: list.y + 22 }, (at) => {
    a.put({ ghost: { on: true, dropped: false, at } })
  }, { duration: 1400, cursor: 'preserve' })

  // 2. Drop: the upload row opens right under it and its bar fills
  a.mark('drop')
  await a.set((s) => ({ up: true, ghost: { ...s.ghost, dropped: true } }))
  await a.wait(450)
  for (const [p, ms] of [[0.35, 520], [0.7, 520], [1, 600]] as const) {
    await a.set({ progress: p })
    await a.wait(ms)
  }

  // 3. Uploaded: the bar gives way to the row's actions, the tint fades
  a.mark('done')
  await a.set({ done: true })
  await a.wait(600)

  // 4. The server's reference number grows in before the filename
  a.mark('kennzeichen')
  await a.set({ kz: true })
  await a.wait(2600)
}

export default function UserUiLoginIllustration({ theme = 'light', strings = DE }: { theme?: Theme; strings?: OgDocLoginStrings }) {
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
      cursor={{ at: { at: 'drop', x: 0.3, y: 0.1 }, idle: 1400 }}
      className="og-login"
    >
      {(s) => (
        <>
          <div className="og-mask">
            <div className="og-pane">
              <div className="og-tabs">
                <span className="og-tab">{t.pane}</span>
              </div>
              <div className="og-bar">
                <span className="og-crumbs">
                  <span className="og-crumb">{t.root}</span>
                  <ChevronRight size={14} strokeWidth={2} className="og-crumb-sep" />
                  <span className="og-heading">{t.folder}</span>
                </span>
                <Button variant="primary" icon={<Upload size={14} strokeWidth={2} />}>
                  {t.upload}
                </Button>
              </div>
              <div className="og-rows" data-target="drop" data-drop>
                <Collapse open={s.up}>
                  <UploadRow s={s} t={t} />
                </Collapse>
                {t.neighbors.map(([kz, file]) => (
                  <div className="og-row" key={kz}>
                    <span className="og-name">
                      <File size={16} strokeWidth={1.5} className="og-doc" />
                      <span className="og-kz-static">{kz}</span>
                      <span className="og-name__text">{file}</span>
                    </span>
                    <Actions />
                  </div>
                ))}
                <div className="og-row">
                  <span className="og-name">
                    <Folder size={16} strokeWidth={1.5} className="og-doc" />
                    <Bar w={92} h={7} tone={3} />
                  </span>
                  <Actions />
                </div>
                {FILL.map((w, i) => (
                  <div className="og-row" key={i}>
                    <span className="og-name">
                      <File size={16} strokeWidth={1.5} className="og-doc" />
                      <Bar w={56} h={7} tone={2} />
                      <Bar w={w} h={7} tone={3} />
                    </span>
                    <Actions />
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Mounted only once the carry starts; it stays mounted after the
              drop for its exit fade. */}
          {s.ghost.on ? (
            <DragImage ghost={s.ghost}>
              <span className="og-pdf">
                <span className="og-pdf__band">PDF</span>
              </span>
              <span className="og-drag__name">{t.file}</span>
            </DragImage>
          ) : null}
        </>
      )}
    </Stage>
  )
}

/** The row's quiet action slots (edit, history, attachments in the product). */
function Actions() {
  return (
    <span className="og-actions">
      <Bar w={20} h={20} r={5} tone={1} />
      <Bar w={20} h={20} r={5} tone={1} />
      <Bar w={20} h={20} r={5} tone={1} />
    </span>
  )
}

function UploadRow({ s, t }: { s: S; t: OgDocLoginStrings }) {
  return (
    <div className="og-row og-new" data-done={s.done || undefined}>
      <span className="og-name">
        <File size={16} strokeWidth={1.5} className="og-doc og-doc--new" />
        {/* Opens its own width, so the filename moves aside instead of jumping */}
        <span className="og-kz" data-show={s.kz || undefined}>
          <span className="og-kz__inner">{t.kennzeichen}</span>
        </span>
        <span className="og-name__text">{t.file}</span>
      </span>
      <States
        value={s.done ? 'done' : 'up'}
        align="end"
        states={{
          up: <Meter className="og-track" value={s.progress} />,
          done: <Actions />,
        }}
      />
    </div>
  )
}

/** The drag image follows the cursor on the drag's own frame callback and
 *  lights whatever [data-drop] it is over (as examples/upload). */
function DragImage({ ghost, children }: { ghost: Ghost; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const over = useRef<HTMLElement | null>(null)
  const following = ghost.on && !ghost.dropped
  const { x, y } = ghost.at
  // The image fades in only once its whole box is inside the stage: the
  // cursor crosses the edge, a cut drag image never shows (framing.md, rule 4)
  const inside = x - 22 >= 12
  useLayoutEffect(() => {
    const root = ref.current?.closest<HTMLElement>('.ss-stage')
    let next: HTMLElement | null = null
    if (following && root) {
      const box = root.getBoundingClientRect()
      const scale = box.width / root.offsetWidth || 1
      const hit = document.elementFromPoint(box.left + x * scale, box.top + y * scale)?.closest<HTMLElement>('[data-drop]') ?? null
      if (hit && root.contains(hit)) next = hit
    }
    if (next !== over.current) {
      over.current?.removeAttribute('data-over')
      next?.setAttribute('data-over', '')
      over.current = next
    }
  }, [following, x, y])
  useLayoutEffect(() => () => over.current?.removeAttribute('data-over'), [])
  // The image hangs below-right of the arrow's tip, as a dragged file does
  return (
    <div ref={ref} className="og-drag" data-show={(following && inside) || undefined} data-dropped={ghost.dropped || undefined} style={{ transform: `translate(${x - 22}px, ${y - 20}px)` }} aria-hidden>
      <div className="og-drag__inner">{children}</div>
    </div>
  )
}
