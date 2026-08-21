import SectionHeader from '@/ui/SectionHeader'
import { type Drilldown, type ProofRow } from '@/lib/api'
import { Empty, codeSpans, sentence } from './bits'

// The Proof tab: what was claimed, what was checked, and what the check found.

// One row per BUILT THING, each carrying its own validation and verification.
//
// The join is mechanical, on plan task ids, and assembled server-side.
type Verified = ProofRow['verified'][number]

/* A CARD, in the same vocabulary every other Quick View block uses, because this tab must read as
   the same surface.
   
   Flat ruled sections were tried and discarded: cards carry the separation, and colour is reserved
   for urgency. */

function ProofSection({ title, meta, children }: {
  title: string; meta?: React.ReactNode; children: React.ReactNode
}) {
  return (
    <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-2">
        <SectionHeader>{title}</SectionHeader>
        {meta}
      </div>
      <div className="mt-2">{children}</div>
    </section>
  )
}

/* One fixed-width status column, so every glyph in the pane lands on the SAME vertical line and
   the eye can run down it to read state without reading a word. */
function Glyph({ tone, children }: { tone: string; children: React.ReactNode }) {
  return <span className={`w-3.5 shrink-0 text-center text-[12px] leading-[1.45] ${tone}`}>{children}</span>
}

/* A PILL, not a floating word: the words differ in length, so a bare span left a ragged right
   edge. */
function StatePill({ v }: { v: Verified }) {
  const [tone, label] =
    !v.ran ? ['border-line text-faint', 'not run yet']
    : v.deferred ? ['border-line text-muted', 'deferred']
    : v.passed ? ['border-success/40 bg-success/10 text-success', historyGlyph(v.history, true)]
    : ['border-danger/40 bg-danger/10 text-danger', historyGlyph(v.history, false)]
  return (
    <span className={`shrink-0 rounded-full border px-1.5 py-px text-[10px] font-medium ${tone}`}>
      {label}
    </span>
  )
}

export function ProofPane({ rows, auths, lenses }: {
  rows: ProofRow[]; auths: Drilldown['authorizations']; lenses: Drilldown['lenses']
}) {
  const real = rows.filter((r) => r.built.length || r.validated.length || r.verified.length || r.task)
  if (!real.length && !lenses.length) {
    return <Empty>No tasks yet — the plan writes them, with the checks that prove them.</Empty>
  }
  const tasks = rows.filter((r) => r.task)
  const doneCount = tasks.filter((r) => r.done).length
  const itemWide = rows.find((r) => !r.task)
  // The join runs check to tasks, so render it that way and fold the fan-out back.
  const byCheck = new Map<string, { v: Verified; covers: string[] }>()
  for (const r of rows) {
    for (const v of r.verified) {
      const seen = byCheck.get(v.check)
      if (seen) { if (r.task) seen.covers.push(r.task) } else {
        byCheck.set(v.check, { v, covers: r.task ? [r.task] : [] })
      }
    }
  }
  const checks = [...byCheck.values()]
  return (
    <div className="space-y-3">
      {/* One contrast rule: the SENTENCE reads at full strength, its NAME stays quiet.
          
          Three sections, in reading order. */}
      {tasks.length > 0 && (
        <ProofSection title="Tasks"
                 meta={<span className="text-[11px] tabular-nums text-muted">{doneCount}/{tasks.length}</span>}>
          {/* A rail instead of a sentence: "how much is done" is answered before any word is read. */}
          <div className="mb-2 h-[3px] overflow-hidden rounded-full bg-hover">
            <div className="h-full rounded-full bg-success transition-[width]"
                 style={{ width: `${Math.round((doneCount / tasks.length) * 100)}%` }} />
          </div>
          <ul className="space-y-2">
            {tasks.map((r) => (
              <li key={r.task} className="flex gap-2">
                <Glyph tone={r.done ? 'text-success' : 'text-faint'}>{r.done ? '✓' : '·'}</Glyph>
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] leading-snug">
                    {/* The id is what a check's `covers` chips point at, so it has to be visible. */}
                    <span className="mr-1.5 font-mono text-[10px] text-faint">{r.task}</span>
                    {/* Quiet, not struck through: a column of struck-out text reads as cancelled
                        work. */}
                    <span className={r.done ? 'text-muted' : 'text-fg'}>{codeSpans(sentence(r.text))}</span>
                  </div>
                  {/* The specification, folded: real detail, but not the answer the tab is open
                      for. */}
                  {r.detail && (
                    <details className="group mt-0.5">
                      <summary className="cursor-pointer list-none text-[11px] text-faint
                                          transition hover:text-muted">
                        <span className="inline-block transition group-open:rotate-90">▸</span>{' '}
                        what this covers
                      </summary>
                      <p className="mt-1 border-l border-line pl-2 text-[12px] leading-snug text-muted">
                        {codeSpans(sentence(r.detail))}
                      </p>
                    </details>
                  )}
                  {/* Build's own evidence, under the task it evidences, so it reads as support
                      rather than more tasks. */}
                  {/* Real bullets. These entries WRAP, so the gap between them must beat the gap
                      inside one. */}
                  {r.validated.length > 0 && (
                    <ul className="mt-1 list-disc space-y-2 border-l border-line pl-5 text-[12px]
                                   leading-snug text-muted marker:text-faint">
                      {r.validated.map((v, i) => <li key={i}>{codeSpans(sentence(v))}</li>)}
                    </ul>
                  )}
                </div>
              </li>
            ))}
          </ul>
          {/* Work that named no task: a whole-suite run is not per-task and never was. */}
          {/* One label for the GROUP: a bare tag on every row named nothing on its own. */}
          {itemWide && itemWide.validated.length > 0 && (
            <div className="mt-2 border-l border-line pl-2">
              <div className="text-[10px] uppercase tracking-wide text-faint">Across the whole item</div>
              <ul className="mt-0.5 list-disc space-y-2 pl-3.5 text-[12px] leading-snug
                             text-muted marker:text-faint">
                {itemWide.validated.map((v, i) => <li key={i}>{codeSpans(sentence(v))}</li>)}
              </ul>
            </div>
          )}
        </ProofSection>
      )}
      {/* THE EXAM, once: every check in plan order, each naming the tasks it defends. */}
      {/* The ACTIVITY, not a past-tense claim: "Verified" above an unrun check is a lie the layout
          tells. */}
      {checks.length > 0 && (
        <ProofSection title="Verification"
                 meta={<span className="text-[11px] tabular-nums text-muted">
                   {checks.filter((c) => c.v.ran && c.v.passed).length}/{checks.length}
                 </span>}>
          <ul className="space-y-3">
            {checks.map(({ v, covers }) => (
              <li key={v.check}>
                {/* Leads with what it PROVES, the plan's own sentence. Older plans fall back
                    rather than blanking. */}
                <div className="flex items-baseline justify-between gap-3">
                  <span className="min-w-0 text-[13px] leading-snug text-fg">
                    {codeSpans(sentence(v.proves || v.expect || v.check))}
                  </span>
                  {/* A check the loop has not reached is NOT a failure, and a cross would say the
                      exam failed. */}
                  <StatePill v={v} />
                </div>
                {/* Ids rather than repetition — naming the tasks is what lets the pane show each
                    check once. */}
                {covers.length > 0 && (
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    <span className="text-[10px] text-faint">covers</span>
                    {covers.map((id) => (
                      <span key={id} className="rounded bg-hover px-1 py-px font-mono text-[10px] text-muted">
                        {id}
                      </span>
                    ))}
                  </div>
                )}
                {/* Folded away: all evidence, none of it the answer to "did this hold". The
                    DIAGNOSIS stays open. */}
                <details className="mt-0.5 group">
                  <summary className="cursor-pointer list-none text-[11px] text-faint
                                      transition hover:text-muted">
                    <span className="inline-block transition group-open:rotate-90">▸</span>{' '}
                    how this was checked
                  </summary>
                  <div className="mt-1 space-y-1 border-l border-line pl-2">
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[11px] text-faint">
                      <span className="font-mono">{v.check}</span>
                      {v.mode && <span>· {v.mode}</span>}
                      {/* Provenance only when it is the stronger claim: agent-attested is the norm
                          and needs no badge. */}
                      {v.by === 'machine' && (
                        <span className="rounded bg-hover px-1 tracking-wide"
                              title="Run by the kernel in the sandbox — no agent between the exit code and this verdict">
                          machine-run
                        </span>
                      )}
                      {v.source && <span>· {v.source}</span>}
                    </div>
                    {v.expect && (
                      <p className="text-[13px] leading-snug text-muted">
                        expects {codeSpans(v.expect)}
                      </p>
                    )}
                    {v.how && (
                      <pre className="max-h-28 overflow-auto whitespace-pre-wrap rounded bg-hover px-1.5 py-1 font-mono text-[11px] leading-relaxed text-muted">{v.how}</pre>
                    )}
                  </div>
                </details>
                {/* A rubric is judged criterion by criterion, so it shows that way: "2/3" would
                    hide which one missed. */}
                {(v.criteria.length > 0 || v.rubric.length > 0) && (
                  <ul className="mt-1 space-y-2">
                    {(v.criteria.length ? v.criteria
                                        : v.rubric.map((text) => ({ text, met: null })))
                      .map((c, i) => (
                      <li key={i} className="flex gap-1.5 text-[13px] leading-snug">
                        <span className={`shrink-0 ${
                          c.met === null ? 'text-faint'
                          : c.met ? 'text-success' : 'text-danger'}`}>
                          {c.met === null ? '·' : c.met ? '✓' : '✗'}
                        </span>
                        <span className={c.met === false ? 'text-fg' : 'text-muted'}>
                          {codeSpans(sentence(c.text))}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                {/* The located cause first: "where it broke" is the line the reader wants, output
                    is the support. */}
                {!v.passed && !v.deferred && v.why && (
                  <p className="mt-0.5 text-[13px] leading-snug text-muted">
                    <span className="font-medium text-fg">{codeSpans(v.where)}</span>
                    {' — '}{codeSpans(v.why)}
                    {v.unknown && (
                      <span className="text-faint"> · undetermined: {codeSpans(v.unknown)}</span>
                    )}
                  </p>
                )}
                {/* Verbatim, because the failure IS the expected-versus-actual, and a tooltip says
                    nothing. */}
                {!v.passed && !v.deferred && v.result && (
                  <pre className="mt-0.5 max-h-28 overflow-auto whitespace-pre-wrap rounded bg-hover px-1.5 py-1 font-mono text-[11px] leading-relaxed text-muted">{v.result}</pre>
                )}
              </li>
            ))}
          </ul>
        </ProofSection>
      )}
      {/* The lenses, last, under the QUESTION each asks. One with nothing to report still shows
          what it probed. */}
      {lenses.length > 0 && (
        <ProofSection title="Also looked at">
          <ul className="space-y-3">
            {lenses.map((l) => (
              <li key={l.lens}>
                {/* The question is the heading and is NAMED as well as asked, at full contrast. */}
                <div className="text-[12.5px] font-medium leading-snug text-fg">
                  <span>{sentence(l.lens)}:</span>{' '}
                  {LENS_QUESTION[l.lens] ?? ''}
                </div>
                <ul className="mt-1 list-disc space-y-2 border-l border-line pl-5 text-[12px]
                               leading-snug text-muted marker:text-faint">
                  {l.probed.map((probe, i) => <li key={i}>{codeSpans(sentence(probe))}</li>)}
                </ul>
                {/* Findings stand off the probe list: a finding is a different kind of statement. */}
                {l.findings.length > 0 && (
                <div className="mt-3 space-y-2">
                {l.findings.map((f, i) => (
                  <p key={i} className="flex items-baseline gap-1.5 text-[12.5px] leading-snug">
                    {/* A label, not a chip: `bg-hover` belongs to code alone, and severity is
                        carried by colour and case. */}
                    <span className={`shrink-0 text-[10px] font-semibold uppercase tracking-wide ${
                      f.severity === 'high' ? 'text-danger'
                      : f.severity === 'medium' ? 'text-warn' : 'text-faint'}`}>
                      {f.severity} severity
                    </span>
                    <span className="min-w-0 text-fg">{codeSpans(sentence(f.text))}</span>
                  </p>
                ))}
                </div>
                )}
              </li>
            ))}
          </ul>
        </ProofSection>
      )}
      {/* Only the state that ASKS something gets a line: an absence footer spends a permanent row
          saying nothing happened. */}
      {auths.length > 0 && (
        <p className="text-[11px] text-warn">
          {auths.length} authorization{auths.length === 1 ? '' : 's'} pending your grant/deny
        </p>
      )}
    </div>
  )
}

// What each lens ASKS, in the owner's words — the slug is the tool's vocabulary. An unmapped lens
// falls back to it.
const LENS_QUESTION: Record<string, string> = {
  intent: 'Does this actually solve the problem the item was filed for?',
  safety: 'Can this hurt anything — unsafe evaluation, destructive paths, secrets in the open?',
  robustness: 'Which inputs were tried, and which are still unhandled?',
  performance: 'Is it fast enough, against a budget the plan named?',
}

// `c3 ✗→✓` — a check that failed then passed. Latest-only would hide the loop's whole story.
function historyGlyph(history: { cycle: number | null; passed: boolean }[], passed: boolean): string {
  if (history.length < 2) return passed ? '✓' : '✗'
  const marks = history.map((h) => (h.passed ? '✓' : '✗'))
  const collapsed = marks.filter((m, i) => i === 0 || m !== marks[i - 1])
  return collapsed.length < 2 ? (passed ? '✓' : '✗') : collapsed.join('→')
}

// ── Reports ─────────────────────────────────────────────────────────────────────────────────────
