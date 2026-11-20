import { ChevronDown, ChevronRight, File, Folder, ShieldCheck, Upload, X } from 'lucide-react'
import { Bar, Button, Stage, type Theme } from '../kit'
import '../kit/stageshow.css'
import '../look-ogdoc.css'
import './readme-hero.css'

// README/docs hero still (P83-S14, ADR 0237 Decision 4: the docs hero slot).
// Exported once per theme with film.mjs --still into docs/assets/ and shown
// through a <picture> in README.md; see REHEARSAL.md beside this file.
//
// OG Doc's documents workspace as the user sees it: the Explorer group on
// the left with the selected invoice, its preview group on the right with
// the page. The workspace runs off the right edge (corner bleed: whole at
// the top-left, cut at the right, fading at the foot). A satellite card sits
// over the preview's well beside the page: the document's signature is
// valid. One picture of
// what the system is for - filed, numbered, previewed, signed - without
// pretending to show every feature. Fade crop as the login illustrations
// (the page is the ground, the workspace dissolves at its foot).
//
// Compact (358 x 420, below 600px - GitHub's README on a phone): a tighter
// crop around the same story, not the wide scene shrunk. Only the Explorer
// group with the selected row, the card straddling its right edge over the
// filler rows; the preview group and the root crumb give way.
//
// Real words only on the story: the folder, the selected document with its
// reference number and its two neighbors, the signature result. Everything
// else is bars.

export type ReadmeHeroStrings = {
  explorer: string
  root: string
  folder: string
  upload: string
  selected: [kennzeichen: string, file: string]
  neighbors: [kennzeichen: string, file: string][]
  version: string
  versionValue: string
  vendor: string
  invoiceNo: string
  totalLabel: string
  total: string
  signatures: string
  valid: string
  level: string
}

const DE: ReadmeHeroStrings = {
  explorer: 'Explorer',
  root: 'Start',
  folder: 'Eingangsrechnungen',
  upload: 'Hochladen',
  selected: ['2026-114', 'Rechnung_Mai.pdf'],
  neighbors: [
    ['2026-113', 'Rechnung_April.pdf'],
    ['2026-112', 'Rechnung_März.pdf'],
  ],
  version: 'Version auswählen',
  versionValue: 'Version 2',
  vendor: 'Bürobedarf Nord GmbH',
  invoiceNo: 'Rechnung Nr. R-4471',
  totalLabel: 'Summe',
  total: '1.240,00 €',
  signatures: 'Signaturen',
  valid: 'Signatur gültig',
  level: 'SES',
}

// Filler rows below the real ones, carrying on into the fade - no more of
// them than there are real rows (craft.md, Restraint)
const FILL = [128, 104, 116]
// The invoice page's line items as bar pairs; enough of them to run past the
// workspace's foot, so the page bleeds off the bottom instead of floating
const FOOTER = [180, 150, 168, 120, 176, 140, 160, 132]
const ITEMS = [96, 120, 84, 108].map((w, i) => [w, 28 + (i % 4) * 2])

export default function ReadmeHero({ theme = 'light', strings = DE }: { theme?: Theme; strings?: ReadmeHeroStrings }) {
  const t = strings
  const [kz, file] = t.selected
  return (
    <Stage
      width={960}
      height={440}
      compact={{ below: 600, width: 358, height: 420 }}
      theme={theme}
      className="og og-hero"
    >
      {() => (
        <>
          <div className="og-mask">
            <div className="og-pane og-hero__workspace">
              {/* Explorer group */}
              <section className="og-hero__group og-hero__explorer">
                <div className="og-tabs">
                  <span className="og-tab" data-on>{t.explorer}</span>
                </div>
                <div className="og-bar">
                  <span className="og-crumbs">
                    <span className="og-crumb og-hero__root">{t.root}</span>
                    <ChevronRight size={14} strokeWidth={2} className="og-crumb-sep og-hero__root" />
                    <span className="og-heading">{t.folder}</span>
                  </span>
                  <Button variant="primary" icon={<Upload size={14} strokeWidth={2} />}>
                    {t.upload}
                  </Button>
                </div>
                <div className="og-list">
                  {/* Folders list first, as in the product */}
                  <div className="og-row">
                    <span className="og-name">
                      <Folder size={16} strokeWidth={1.5} className="og-icon" />
                      <Bar w={92} h={7} tone={4} />
                    </span>
                  </div>
                  <div className="og-row og-hero__selected">
                    <span className="og-name">
                      <File size={16} strokeWidth={1.5} className="og-icon" data-focus />
                      <span className="og-hero__kz og-hero__kz--on">{kz}</span>
                      <span className="og-name__text">{file}</span>
                    </span>
                  </div>
                  {t.neighbors.map(([nkz, nfile]) => (
                    <div className="og-row" key={nkz}>
                      <span className="og-name">
                        <File size={16} strokeWidth={1.5} className="og-icon" />
                        <span className="og-hero__kz">{nkz}</span>
                        <span className="og-name__text">{nfile}</span>
                      </span>
                    </div>
                  ))}
                  {FILL.map((w, i) => (
                    <div className="og-row" key={i}>
                      <span className="og-name">
                        <File size={16} strokeWidth={1.5} className="og-icon" />
                        <Bar w={56} h={7} tone={3} />
                        <Bar w={w} h={7} tone={4} />
                      </span>
                    </div>
                  ))}
                </div>
              </section>

              {/* Preview group: the selected document's tab and its page */}
              <section className="og-hero__group og-hero__preview">
                <div className="og-tabs">
                  <span className="og-tab" data-on>
                    {kz} {file}
                    <X size={13} strokeWidth={2} className="og-hero__close" />
                  </span>
                </div>
                {/* PreviewPane's own head: the version picker (the title is
                    already the tab's - say it once) */}
                <div className="og-bar og-hero__preview-head">
                  <span className="og-hero__version">
                    <span className="og-meta">{t.version}</span>
                    <span className="og-hero__select">
                      {t.versionValue}
                      <ChevronDown size={14} strokeWidth={2} className="og-crumb-sep" />
                    </span>
                  </span>
                </div>
                <div className="og-hero__stage">
                  <div className="og-hero__page">
                    <div className="og-hero__letterhead">
                      <span className="og-hero__vendor">{t.vendor}</span>
                      <Bar w={20} h={5} tone={3} />
                    </div>
                    <span className="og-hero__invoice-no">{t.invoiceNo}</span>
                    <Bar w={84} h={5} tone={3} />
                    {/* Line items straight in the page's column, a rule above */}
                    <div className="og-hero__rule" />
                    {ITEMS.map(([a, b], i) => (
                      <div className="og-hero__item" key={i}>
                        <Bar w={a} h={5} tone={3} />
                        <Bar w={b} h={5} tone={4} />
                      </div>
                    ))}
                    <div className="og-hero__item og-hero__total">
                      <span className="og-meta">{t.totalLabel}</span>
                      <span className="og-hero__amount">{t.total}</span>
                    </div>
                    {/* Payment terms and bank details: filler that carries the
                        page past the workspace's foot, so it bleeds */}
                    <div className="og-hero__footer">
                      {FOOTER.map((w, i) => (
                        <Bar key={i} w={w} h={5} tone={3} />
                      ))}
                    </div>
                  </div>
                </div>
              </section>
            </div>
          </div>

          {/* Satellite: the signature panel's verdict for this document */}
          <div className="og-card og-hero__card">
            <div className="og-hero__card-head">{t.signatures}</div>
            <div className="og-hero__verdict">
              <ShieldCheck size={18} strokeWidth={1.75} className="og-hero__shield" />
              <span className="og-hero__valid">{t.valid}</span>
              <span className="og-hero__level">{t.level}</span>
            </div>
            <div className="og-hero__card-row">
              <Bar w={72} h={5} tone={2} />
              <Bar w={48} h={5} tone={3} />
            </div>
            <div className="og-hero__card-row">
              <Bar w={56} h={5} tone={2} />
              <Bar w={64} h={5} tone={3} />
            </div>
          </div>
        </>
      )}
    </Stage>
  )
}
