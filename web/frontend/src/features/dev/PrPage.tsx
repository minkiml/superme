import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import {
  GitMerge, Loader2, ChevronRight, ChevronDown, FileDiff, Check, GitCommitHorizontal,
  ChevronsUpDown,
} from 'lucide-react'
import Markdown from '@/ui/Markdown'
import {
  parsePatch, foldContext, pairRows, highlight, langFor, type Row,
} from './diff'
import {
  getWorkItemPr, getWorkItemPrDiff, advanceWorkItem,
  type PrView, type PrDiff,
} from '@/lib/api'
import { useViewportWidth, PANE } from '@/lib/layout'

// The dedicated PR page (renovation §4.4) — `strict`'s review surface, and readable in any mode.
//
// It is a PAGE, not a panel: `main.tsx` mounts it as the whole document when the URL carries
// `?repo=&pr=`, and the Git tab's button opens that URL in a new browser tab. It was an in-app
// overlay first, squeezed between the cockpit behind it and the chat rail beside it, and a diff
// read in a third of a screen is a diff nobody reads. The cockpit stays where it was, in its own
// window, with the item's chat — the place the opinion is voiced, since `revise` is the only way
// back (§2.1).
//
// Left: `report-review.md`, the arc the review run wrote. Right: the branch, GROUPED BY TASK off
// the commits' `SuperMe-Task` trailers — the thing an ordinary forge cannot do, because ordinary
// branches aren't task-labelled by construction. Inside a group, files are churn-ranked: the
// biggest change is where the risk is, and it's what a reader who stops after two files should
// have seen.
//
// One action: Merge, which is the ordinary review approve (approve = merge, §2.3). There is no
// Reject — a change that isn't good enough is said in the item's chat. Slice 6 restyles this page.

const SPLIT_KEY = 'superme.pr.split'

// The draggable divider. Pointer events (not mouse) so a trackpad, a pen and a touchscreen all
// work, and `setPointerCapture` so a fast drag that outruns the 5px bar keeps dragging instead of
// dropping the moment the cursor leaves it. Clamped to 20–80% — a pane dragged to nothing is a
// pane the reader then has to discover how to bring back. Arrow keys move it too: the bar is a
// real control, so it takes focus and answers the keyboard like one.
function Splitter({ split, onSplit }: {
  split: number
  onSplit: Dispatch<SetStateAction<number>>
}) {
  const clamp = (n: number) => Math.min(80, Math.max(20, n))
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-valuenow={Math.round(split)}
      aria-label="Resize the report and walkthrough panes"
      tabIndex={0}
      onPointerDown={(e) => {
        e.preventDefault()
        e.currentTarget.setPointerCapture(e.pointerId)
        const move = (ev: PointerEvent) => onSplit(clamp((ev.clientX / window.innerWidth) * 100))
        const up = () => {
          window.removeEventListener('pointermove', move)
          window.removeEventListener('pointerup', up)
          document.body.style.userSelect = ''
        }
        // Without this a drag across the report selects its text as it goes.
        document.body.style.userSelect = 'none'
        window.addEventListener('pointermove', move)
        window.addEventListener('pointerup', up)
      }}
      // Updater form, not `clamp(split - 2)`: a HELD arrow key auto-repeats faster than React
      // commits, and every repeat in one batch would read the same stale `split` and land on the
      // same value — the key would appear to move it once and then stick.
      onKeyDown={(e) => {
        if (e.key === 'ArrowLeft') { e.preventDefault(); onSplit((s) => clamp(s - 2)) }
        if (e.key === 'ArrowRight') { e.preventDefault(); onSplit((s) => clamp(s + 2)) }
      }}
      onDoubleClick={() => onSplit(44)}
      title="Drag to resize · double-click to reset"
      className="group relative w-px shrink-0 cursor-col-resize bg-line outline-none"
    >
      {/* The hit area is 9px wide while the LINE stays 1px — a 1px grab target is a 1px target.
          The wider strip is invisible until hover/focus, so the layout reads as one hairline. */}
      <span className="absolute inset-y-0 -left-1 -right-1 transition-colors group-hover:bg-accent/40 group-focus:bg-accent/60" />
    </div>
  )
}

export default function PrPage({ itemId, contextId }: {
  itemId: string
  contextId: string
}) {
  const [pr, setPr] = useState<PrView | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [merging, setMerging] = useState(false)
  // The divider position, as the LEFT pane's % of the page (owner, 2026-08-09). Which side needs
  // the room is the reader's question, not ours: a prose-heavy review wants the left, a wide diff
  // wants the right, and the same fixed 44/56 is wrong for both. Persisted per browser so the
  // choice survives the reload a merge triggers and every later PR tab opens where they left it.
  const [split, setSplit] = useState<number>(() => {
    const saved = Number(localStorage.getItem(SPLIT_KEY))
    return Number.isFinite(saved) && saved >= 20 && saved <= 80 ? saved : 44
  })
  useEffect(() => { localStorage.setItem(SPLIT_KEY, String(split)) }, [split])
  // The PR page owns the whole window, so the window IS its container.
  const narrow = useViewportWidth() < PANE.mid + PANE.narrow

  // `keepErr` is what makes a refused merge readable: the refusal itself CHANGES the item (the
  // freshness re-vet syncs the branch and moves the phase), so the catch below reloads — and a
  // plain reload used to clear `err` on success, erasing the daemon's explanation in the same tick
  // it was set. The owner then saw a Merge that did nothing and said nothing. A reload the OWNER
  // triggers still clears; only the one a refusal triggers keeps it.
  const load = (keepErr = false) => {
    getWorkItemPr(itemId, contextId).then((d) => { setPr(d); if (!keepErr) setErr(null) })
      .catch((e) => setErr(String(e)))
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => load(), [itemId, contextId])

  // Several of these can be open at once, so the tab has to say which item it is holding.
  useEffect(() => {
    document.title = pr?.branch ? `PR · ${pr.branch}` : `PR · ${itemId}`
  }, [pr?.branch, itemId])

  async function merge() {
    setMerging(true)
    setErr(null)
    try {
      await advanceWorkItem(itemId, contextId)
      load()   // stay open on the merged state — the cockpit window's own poll picks the rest up
    } catch (e) {
      setErr(String(e))
      load(true)   // a refused merge (freshness park / re-vet) changed the item — show what it is
                   // now, WITHOUT wiping the reason it was refused
    } finally {
      setMerging(false)
    }
  }

  return (
    <div className="fixed inset-0 flex flex-col bg-app font-sans text-fg">
      <header className="flex shrink-0 items-center gap-3 border-b border-line px-4 py-2.5">
        <span className="text-[11px] font-medium uppercase tracking-wide text-faint">SuperMe · PR</span>
        <span className="font-mono text-[12.5px] text-fg">
          {pr?.branch ?? '…'} <span className="text-faint">→</span> {pr?.target ?? '—'}
        </span>
        {pr && (
          <span className="text-[11.5px] text-muted">
            {pr.stat.commits} commit{pr.stat.commits === 1 ? '' : 's'} ·{' '}
            <span className="text-success">+{pr.stat.insertions}</span>{' '}
            <span className="text-danger">−{pr.stat.deletions}</span> · {pr.stat.files} file
            {pr.stat.files === 1 ? '' : 's'}
          </span>
        )}
        <div className="flex-1" />
        {pr?.merged ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-success">
            <Check size={14} /> merged {pr.merge_commit?.slice(0, 10)}
          </span>
        ) : pr?.terminal ? (
          // Finished WITHOUT landing — abandoned or superseded. The branch is still there and the
          // diff is still worth reading, but the decision has been taken and it was "no". Offering
          // Merge here contradicts it; the daemon refuses the call anyway (409), so a button would
          // only be a way to be told no.
          <span className="inline-flex items-center gap-1.5 text-xs text-muted">
            <Check size={14} /> {pr.outcome ?? 'finished'} — never merged, branch left in place
          </span>
        ) : (
          <button
            onClick={merge}
            disabled={merging || !pr}
            title="Merge — the review approve: squashes this branch onto the anchor, applies the staged knowledge delta, then advances to close. On a moved anchor it holds here and says why."
            className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-on-accent transition hover:opacity-90 disabled:opacity-50"
          >
            {merging ? <Loader2 size={14} className="animate-spin" /> : <GitMerge size={14} />} Merge
          </button>
        )}
        {/* No close button: this is a browser tab, and the browser already has one. A script
            `window.close()` is refused for tabs the page didn't itself open, so the control would
            have looked live and done nothing. */}
      </header>

      {err && (
        <div className="shrink-0 border-b border-line bg-danger/10 px-4 py-2 text-xs text-danger">{err}</div>
      )}

      {/* Below the width where two readable panes fit, they STACK and the splitter goes with them
          (`lib/layout`): the report reads first, the walkthrough under it, both full width. Two
          200px columns of prose and code side by side is not a smaller version of this page — it
          is a different, unreadable one. */}
      <div className={`flex min-h-0 flex-1 ${narrow ? 'flex-col overflow-y-auto' : ''}`}>
        {/* Left — the review report: whether this should land at all. What to know while READING a
            task's code lives with that task on the right, so the two panes never say the same thing
            twice. */}
        <div
          className={narrow ? 'shrink-0 border-b border-line px-5 py-4' : 'min-w-0 overflow-y-auto px-5 py-4'}
          style={narrow ? undefined : { width: `${split}%` }}
        >
          {pr?.report
            ? <Markdown text={pr.report} variant="doc" tone="dev" />
            : <p className="text-sm text-faint">No review report yet — it is written when the item enters review.</p>}
        </div>
        {!narrow && <Splitter split={split} onSplit={setSplit} />}
        {/* Right — the walkthrough. */}
        <div className={`min-w-0 flex-1 px-5 py-4 ${narrow ? '' : 'overflow-y-auto'}`}>
          {!pr ? (
            <div className="flex items-center gap-2 py-6 text-sm text-muted">
              <Loader2 size={14} className="animate-spin" /> Reading the branch…
            </div>
          ) : pr.groups.length === 0 ? (
            <p className="text-sm text-faint">
              No commits over {pr.base ?? 'the base'} — nothing to walk through.
            </p>
          ) : (
            <div className="space-y-4">
              {pr.groups.map((g) => (
                <TaskGroup key={g.task ?? 'unlabelled'} group={g} itemId={itemId} contextId={contextId} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

type Group = PrView['groups'][number]

// The review notes, above this task's own commits (owner, 2026-08-09). They answer the question you
// have while reading a diff — what did THIS task have to make true, what should I look at, what
// proves it — and deliberately not the question the review report answers, which is whether to
// merge at all. Nothing here is repeated from that report: an owner moving between the two panes
// should never meet the same sentence twice.
//
// Every row renders only when it has something to say. A task where build found nothing to point at
// and nothing deviated shows just its requirement and its checks, which is the honest picture of an
// unremarkable task — and padding it would teach the owner to skip the block entirely.
function Notes({ group }: { group: Group }) {
  const checks = group.checks ?? []
  if (!group.needed && !group.look && !group.deviated && !checks.length) return null
  return (
    <dl className="mb-2.5 space-y-1.5 rounded-md bg-hover/40 px-2.5 py-2 text-[12px]">
      {group.needed && <Row label="Had to make true">{group.needed}</Row>}
      {/* Marked, because it is the one line here nobody could derive — a person wrote it down
          because the diff cannot show it. */}
      {group.look && <Row label="Look at" tone="text-warn">{group.look}</Row>}
      {group.deviated && <Row label="Left the plan">{group.deviated}</Row>}
      {checks.length > 0 && (
        <Row label="Proven by">
          <span className="inline-flex flex-wrap gap-x-2 gap-y-1">
            {checks.map((c) => (
              <span key={c.id} className="font-mono text-[11px]">
                <span className={!c.ran ? 'text-faint' : c.passed ? 'text-success' : 'text-danger'}>
                  {c.ran ? (c.passed ? '✓' : '✗') : '○'}
                </span>{' '}
                <span className={c.ran ? 'text-muted' : 'text-faint'}>{c.id}</span>
                {c.deferred && <span className="text-faint"> deferred</span>}
              </span>
            ))}
          </span>
        </Row>
      )}
    </dl>
  )
}

function Row({ label, tone, children }: {
  label: string; tone?: string; children: React.ReactNode
}) {
  return (
    <div className="flex gap-2">
      <dt className="w-[86px] shrink-0 text-[10.5px] uppercase tracking-wide text-faint">{label}</dt>
      <dd className={`min-w-0 flex-1 ${tone ?? 'text-muted'}`}>{children}</dd>
    </div>
  )
}

function TaskGroup({ group, itemId, contextId }: { group: Group; itemId: string; contextId: string }) {
  const [open, setOpen] = useState(true)
  const plus = group.files.reduce((a, f) => a + f.plus, 0)
  const minus = group.files.reduce((a, f) => a + f.minus, 0)
  return (
    <section className="rounded-lg border border-line bg-surface">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-start gap-2 px-3 py-2.5 text-left hover:bg-hover/50"
      >
        {open ? <ChevronDown size={14} className="mt-0.5 shrink-0 text-faint" />
              : <ChevronRight size={14} className="mt-0.5 shrink-0 text-faint" />}
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-[11px] text-accent-text">{group.task ?? 'unlabelled'}</span>
            <span className="min-w-0 flex-1 truncate text-[13px] text-fg">
              {group.title ?? (group.task
                ? 'not in the plan’s task list'
                : 'commits with no task trailer')}
            </span>
          </div>
          <div className="mt-0.5 text-[11px] text-faint">
            {group.commits.length} commit{group.commits.length === 1 ? '' : 's'} ·{' '}
            <span className="text-success">+{plus}</span> <span className="text-danger">−{minus}</span> ·{' '}
            {group.files.length} file{group.files.length === 1 ? '' : 's'}
          </div>
        </div>
      </button>
      {open && (
        <div className="border-t border-line px-3 py-2.5">
          <Notes group={group} />
          <ul className="mb-2.5 space-y-1">
            {group.commits.map((c) => (
              <li key={c.sha} className="flex items-start gap-2 text-[12px]">
                <GitCommitHorizontal size={12} className="mt-1 shrink-0 text-faint" />
                <span className="font-mono text-[10.5px] text-faint">{c.short}</span>
                <span className="min-w-0 flex-1 text-fg">
                  {c.subject}
                  {c.body && <span className="block whitespace-pre-wrap text-[11.5px] text-muted">{c.body}</span>}
                </span>
              </li>
            ))}
          </ul>
          <div className="space-y-1">
            {group.files.map((f) => (
              <FileRow key={f.path} file={f} task={group.task ?? null} itemId={itemId} contextId={contextId} />
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

function FileRow({ file, task, itemId, contextId }: {
  file: Group['files'][number]; task: string | null; itemId: string; contextId: string
}) {
  const [open, setOpen] = useState(false)
  const [diff, setDiff] = useState<PrDiff | null>(null)
  const [err, setErr] = useState<string | null>(null)
  // Fetched on expand, never up front — a branch's whole diff is what would make this page slow.
  useEffect(() => {
    if (!open || diff) return
    getWorkItemPrDiff(itemId, file.path, task, contextId).then(setDiff).catch((e) => setErr(String(e)))
  }, [open, diff, itemId, file.path, task, contextId])
  return (
    <div className="rounded-md border border-line/60">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-hover/50"
      >
        <FileDiff size={12} className="shrink-0 text-faint" />
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-fg">{file.path}</span>
        <span className="shrink-0 text-[11px]">
          <span className="text-success">+{file.plus}</span> <span className="text-danger">−{file.minus}</span>
        </span>
      </button>
      {open && (
        <div className="border-t border-line/60 bg-sunken">
          {err && <div className="px-2.5 py-1.5 text-[11px] text-danger">{err}</div>}
          {!diff && !err && (
            <div className="flex items-center gap-2 px-2.5 py-2 text-[11px] text-muted">
              <Loader2 size={12} className="animate-spin" /> Loading the patch…
            </div>
          )}
          {diff?.patches.map((p) => (
            <div key={p.sha}>
              <div className="border-b border-line/60 px-2.5 py-1 text-[10.5px] text-faint">
                <span className="font-mono">{p.short}</span> {p.subject}
                {p.truncated && <span className="ml-1 text-warn">· patch truncated</span>}
              </div>
              <Patch text={p.patch} path={file.path} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// --- the patch ------------------------------------------------------------------
// Three things the plain one-colour-per-line version could not do (owner, 2026-08-09): the code
// carries SYNTAX, a replacement reads side by side as "this became that", and untouched context
// folds away so the change is the page. Parsing lives in `./diff`; this is presentation only.

const GUTTER = 'w-8 shrink-0 select-none px-1 text-right text-[9.5px] tabular-nums text-faint'
const ROW_BG: Record<Row['kind'], string> = {
  add: 'bg-success/[0.10]', del: 'bg-danger/[0.10]', ctx: '',
}
// The marker column carries the +/- so the diff still reads correctly to someone who cannot
// distinguish the two tints - colour is never the only channel.
const MARK: Record<Row['kind'], string> = { add: '+', del: '\u2212', ctx: ' ' }

// A COLUMN is one scroll container holding one grid track (owner, 2026-08-09). Three requirements
// settle this shape between them:
//
//  - Scrolling is per column, never per row and never shared. Split shows the before and the after
//    side by side on ONE screen, and each side scrolls to its own longest line; a shared scroller
//    pushed the "after" column off-screen whenever the "before" had a long line.
//  - `minmax(100%, max-content)` + grid stretch is what makes the tints RECTANGULAR. Sizing each
//    row to its own content (`w-max`) gave every row a different width, so a block of deletions
//    had a ragged right edge that traced the code instead of marking the block.
//  - Rows stay one line tall, which is the only reason two independent columns can be trusted to
//    line up without measuring anything.
const COLUMN = 'min-w-0 flex-1 overflow-x-auto'
// `w-max` sizes the track to its WIDEST row, `min-w-full` floors it at the column, and the single
// implicit grid column then stretches every row to that one width. A percentage-minimum track
// (`minmax(100%, max-content)`) does NOT work here: inside a scroll container the percentage
// resolves against the visible width, so the rows all agreed on the wrong number — uniform, but
// clipped at the fold instead of covering the longest line.
const TRACK = 'grid w-max min-w-full'

function Code({ text, lang }: { text: string; lang: string | null }) {
  return (
    <code
      // `shrink-0` is load-bearing: a flex item defaults to shrinking, so a non-wrapping line was
      // squeezed to the column width and clipped — which is why the track never grew past 100%
      // and nothing scrolled. Sized to its content, it pushes the track out instead.
      className="shrink-0 whitespace-pre pr-3"
      dangerouslySetInnerHTML={{ __html: highlight(text, lang) || '&nbsp;' }}
    />
  )
}

function UnifiedRow({ row, lang }: { row: Row; lang: string | null }) {
  return (
    <div className={`flex ${ROW_BG[row.kind]}`}>
      <span className={GUTTER}>{row.oldNo ?? ''}</span>
      <span className={GUTTER}>{row.newNo ?? ''}</span>
      <span className="w-3 shrink-0 select-none text-center text-faint">{MARK[row.kind]}</span>
      <Code text={row.text} lang={lang} />
    </div>
  )
}

/** One side of a split row. An empty half of an unequal replacement is TINTED, not blank: a bare
 *  gap reads as "nothing here", where what happened is "this side has one fewer line". */
function SplitRow({ row, side, lang }: {
  row: Row | null; side: 'del' | 'add'; lang: string | null
}) {
  return (
    <div className={`flex ${row ? ROW_BG[row.kind] : 'bg-line/20'}`}>
      <span className={GUTTER}>{(side === 'del' ? row?.oldNo : row?.newNo) ?? ''}</span>
      {row ? <Code text={row.text} lang={lang} /> : <span>&nbsp;</span>}
    </div>
  )
}

/** The collapsed-context control. In split it is drawn in BOTH columns so the two stay in step —
 *  the twin is inert and hidden from assistive tech, so it reads as the one bar it looks like. */
function Fold({ rows, onExpand, twin = false }: {
  rows: Row[]; onExpand: () => void; twin?: boolean
}) {
  const label = `${rows.length} unchanged lines`
  if (twin) {
    return (
      <div aria-hidden className="bg-hover/40 py-0.5 text-[10px] text-transparent">
        <span className="sticky left-0 px-2.5">{label}</span>
      </div>
    )
  }
  return (
    <button onClick={onExpand} className="bg-hover/40 py-0.5 text-left transition hover:bg-hover">
      <span className="sticky left-0 inline-flex items-center gap-2 px-2.5 text-[10px] text-muted">
        <ChevronsUpDown size={10} className="shrink-0" />
        {label}
      </span>
    </button>
  )
}

function Patch({ text, path }: { text: string; path: string }) {
  const [split, setSplit] = useState(false)
  const [shown, setShown] = useState<Set<string>>(() => new Set())
  const lang = useMemo(() => langFor(path), [path])
  const hunks = useMemo(() => parsePatch(text), [text])
  if (!hunks.length) {
    return <div className="px-2.5 py-2 text-[11px] text-faint">No textual change in this patch.</div>
  }
  return (
    <div className="font-mono text-[10.5px] leading-[1.5]">
      <div className="flex items-center border-b border-line/60 px-2.5 py-1">
        <span className="ml-auto flex overflow-hidden rounded border border-line font-sans text-[9.5px]">
          {([['Unified', false], ['Split', true]] as const).map(([label, v]) => (
            <button
              key={label}
              onClick={() => setSplit(v)}
              className={`px-1.5 py-0.5 transition ${
                split === v ? 'bg-accent text-on-accent' : 'text-muted hover:bg-hover'}`}
            >
              {label}
            </button>
          ))}
        </span>
      </div>
      {/* Hunks are separated, not merely stacked: consecutive hunks ran together into one wall in
          which the `@@` lines were the only cue that the reader had jumped somewhere else in the
          file. A gap plus a rule makes each hunk a block you can see the edges of. */}
      <div className="space-y-2 py-1">
        {hunks.map((h, hi) => {
          const chunks = foldContext(h.rows)
          const expand = (key: string) => () => setShown((s) => new Set(s).add(key))
          const folded = (c: ReturnType<typeof foldContext>[number], key: string) =>
            c.type === 'fold' && !shown.has(key)
          return (
            <div key={hi} className={hi ? 'border-t border-line/60 pt-2' : ''}>
              {/* Outside the scrollers on purpose: the `@@` line names the enclosing function,
                  which is the answer to "where am I" — it must not slide away with the code. */}
              <div className="bg-hover/30 px-2.5 py-0.5 text-[10px] text-accent-text">{h.header}</div>
              {split ? (
                <div className="flex gap-px">
                  {(['del', 'add'] as const).map((side) => (
                    <div key={side} className={COLUMN}>
                      <div className={TRACK}>
                        {chunks.map((c, ci) => {
                          const key = `${hi}:${ci}`
                          if (folded(c, key)) {
                            return <Fold key={key} rows={c.rows} onExpand={expand(key)}
                                         twin={side === 'add'} />
                          }
                          return pairRows(c.rows).map((p, i) => (
                            <SplitRow key={`${key}:${i}`} row={side === 'del' ? p.left : p.right}
                                      side={side} lang={lang} />
                          ))
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className={COLUMN}>
                  <div className={TRACK}>
                    {chunks.map((c, ci) => {
                      const key = `${hi}:${ci}`
                      if (folded(c, key)) {
                        return <Fold key={key} rows={c.rows} onExpand={expand(key)} />
                      }
                      return c.rows.map((r, i) => (
                        <UnifiedRow key={`${key}:${i}`} row={r} lang={lang} />
                      ))
                    })}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
