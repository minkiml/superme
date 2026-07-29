import { useEffect, useState } from 'react'
import {
  GitMerge, Loader2, ChevronRight, ChevronDown, FileDiff, Check, GitCommitHorizontal,
} from 'lucide-react'
import Markdown from '@/ui/Markdown'
import {
  getWorkItemPr, getWorkItemPrDiff, advanceWorkItem,
  type PrView, type PrDiff,
} from '@/lib/api'

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

export default function PrPage({ itemId, contextId }: {
  itemId: string
  contextId: string
}) {
  const [pr, setPr] = useState<PrView | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [merging, setMerging] = useState(false)

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

      <div className="flex min-h-0 flex-1">
        {/* Left — the arc, as the review run wrote it. */}
        <div className="min-w-0 flex-1 overflow-y-auto border-r border-line px-5 py-4">
          {pr?.report
            ? <Markdown text={pr.report} variant="doc" tone="dev" />
            : <p className="text-sm text-faint">No review report yet — it is written when the item enters review.</p>}
        </div>
        {/* Right — the walkthrough. */}
        <div className="min-w-0 flex-[1.25] overflow-y-auto px-5 py-4">
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
              <Patch text={p.patch} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// A unified diff, coloured by line kind. Deliberately plain: the point of this page is that the
// change is grouped and explained, not that the diff is prettier than git's.
function Patch({ text }: { text: string }) {
  return (
    <pre className="overflow-x-auto px-2.5 py-1.5 font-mono text-[10.5px] leading-[1.45]">
      {text.split('\n').map((line, i) => (
        <div
          key={i}
          className={
            line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ')
              || line.startsWith('index ') ? 'text-faint'
            : line.startsWith('@@') ? 'text-accent-text'
            : line.startsWith('+') ? 'text-success'
            : line.startsWith('-') ? 'text-danger'
            : 'text-muted'
          }
        >
          {line || ' '}
        </div>
      ))}
    </pre>
  )
}
