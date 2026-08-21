import { Loader2, FileText, ScrollText, History, FlaskConical, ShieldCheck, Gauge, ListChecks, ChevronRight, CircleDot, Network, Inbox, ClipboardCheck, ClipboardList, KeyRound, Gavel } from 'lucide-react'
import Markdown from '@/ui/Markdown'
import SectionHeader from '@/ui/SectionHeader'
import { type WorkItem, type Drilldown } from '@/lib/api'
import { navigate } from '@/lib/router'
import { PHASE_LABEL, STATUS_LABEL } from '../common'
import { ActionIcon, Row, codeSpans, sentence } from './bits'
import { FromYouCompose } from './ownerInput'

// The Now tab: what this item needs from me, and the card that asks for it.

// The SUBJECT of each check, beside the mark carrying its verdict — mono slugs look alike in a
// list.
//
// An unmapped criterion falls back rather than blanking: the kernel emits an unknown slug when it
// has no evaluator.
const CHECK_ICON: Record<string, typeof FileText> = {
  required_artifacts: FileText,
  children_terminal: Network,
  findings_delivered: ScrollText,
  spawns_exist: Inbox,
  triage_ran: ClipboardCheck,
  plan_complete: ClipboardList,
  vet_plan_sharp: FlaskConical,
  revisions_recorded: History,
  evidence_fresh: ShieldCheck,
  no_pending_authorizations: KeyRound,
  owner_rulings: Gavel,
}

export function NowPane({ d, it, contextId, busy, onAct }: {
  d: Drilldown; it: WorkItem; contextId: string
  busy: string | null; onAct: (id: string) => void
}) {
  // EVERY block is a card: cards carry separation, and colour alone carries urgency.
  return (
    <div className="space-y-3">
      {/* About first: what this item IS precedes what is happening to it. */}
      {/* Rows are server-composed and rendered in order; this reads no label by name, so a relabel
          cannot blank them. */}
      {/* Collapsed: what the item IS is read once, then it stands between the owner and the ask. */}
      {d.about.length > 0 && (
        <details className="group rounded-md border border-line bg-sunken px-3 py-2.5">
          <summary className="flex cursor-pointer list-none items-center gap-1.5">
            <ChevronRight size={13} className="shrink-0 text-faint transition group-open:rotate-90" />
            <SectionHeader className="flex items-center gap-1.5">
              <Gauge size={13} /> About this work-item
            </SectionHeader>
          </summary>
          <dl className="mt-1.5 text-[13px]">
            {d.about.map((r) => (
              <div key={r.label} className="flex gap-2 border-t border-line py-1.5 first:border-t-0">
                <dt className="w-20 shrink-0 text-muted">{r.label}</dt>
                <dd className="min-w-0 flex-1 text-fg">{codeSpans(sentence(r.value))}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}

      {/* Shown where the ruling was GIVEN, so the consequence lands beside the act. */}
      {d.decisions.length > 0 && (
        <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
          <SectionHeader className="flex items-center gap-1.5">
            <Gavel size={13} /> Rules your ruling set
          </SectionHeader>
          <ul className="mt-1.5 space-y-1.5">
            {d.decisions.map((dec) => (
              <li key={dec.id} className="flex gap-2 text-[13px]">
                <span className="shrink-0 font-mono text-[12px] text-muted">{dec.id}</span>
                <span className="min-w-0 flex-1 text-fg">{dec.title}</span>
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-[11px] text-faint">
            Standing in this project's decision ledger — later runs read it before asking again.
          </p>
        </section>
      )}

      {/* The live line is this card's title and the ask lives inside it; split apart, that
          sentence breaks in half. */}
      <section className="rounded-md border border-line bg-sunken px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <CircleDot size={13} className={d.now.running ? 'animate-pulse text-accent' : 'text-faint'} />
          <SectionHeader>{PHASE_LABEL[d.now.phase] ?? d.now.phase}</SectionHeader>
          {d.now.cycle > 0 && <span className="text-[13px] text-muted">· cycle {d.now.cycle}</span>}
          {/* No telemetry here: it is the header's line, and the same numbers twice invite a hunt
              for the difference. */}
        </div>
        {/* What this phase concluded, in its report's words. Absent while it works: a placeholder
            reads as a conclusion. */}
        {/* Labelled, because the sentence is a quotation from another document and unlabelled it
            reads as this pane's own. */}
        {/* A running phase has no summary, so this holds the last completed one, labelled with
            whose it is. */}
        {d.now.summary && (
          <p className="mt-1.5 text-[13px] leading-snug text-fg">
            <span className="font-semibold text-warn">
              {d.now.summary_phase && d.now.summary_phase !== d.now.phase
                ? `${PHASE_LABEL[d.now.summary_phase] ?? d.now.summary_phase} summary:`
                : 'Summary:'}
            </span>{' '}
            {codeSpans(sentence(d.now.summary))}
          </p>
        )}
        {d.attention && (
          <div className="mt-2.5">
            <AttentionCardView card={d.attention} busy={busy} onAct={onAct}
                               contextId={contextId} />
          </div>
        )}
      </section>

      {/* Only while it can still land: after plan starts, a form offering to change it offers
          nothing. */}
      {d.now.phase === 'triage' && <FromYouCompose itemId={it.id} contextId={contextId} />}

      {/* What must resolve, and only that: Approve is greyed and the button alone cannot say why. */}
      {d.blocked_by.length > 0 && d.at_gate && !d.now.running && (
        <section className="rounded-md border border-danger/40 bg-danger/5 px-3 py-2.5">
          <SectionHeader className="mb-1.5 flex items-center gap-1.5 text-danger">
            <ListChecks size={13} /> Must resolve before {d.gate_label}
          </SectionHeader>
          <ul>
            {/* Every row here stops the button, so none carries a severity badge — the same badge
                everywhere says nothing. */}
            {d.checks.filter((c) => !c.ok && c.blocking).map((c) => {
              const Icon = CHECK_ICON[c.criterion] ?? ListChecks
              return (
                <li key={c.criterion} className="border-t border-line py-1.5 text-[13px] first:border-t-0">
                  <div className="flex items-baseline gap-2">
                    <span className="w-3 shrink-0 text-center text-danger">✗</span>
                    <Icon size={12} className="shrink-0 self-center text-faint" />
                    <span className="font-mono text-[11px] font-medium text-muted">{c.criterion}</span>
                  </div>
                  {/* Indented past the mark AND the icon so the sentence hangs under the name. */}
                  <p className="mt-0.5 pl-10 leading-snug text-fg">{c.detail}</p>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {/* No facts chips: kind and deliverable are in the header, and triaged restated the check
          directly above. */}
    </div>
  )
}

// The attention card: WHY, WHAT and REFERENCE, every field server-composed. No leading icon — the
// tint already says it.
function AttentionCardView({ card, busy, onAct, contextId }: {
  card: NonNullable<Drilldown['attention']>; busy: string | null
  onAct: (id: string) => void; contextId: string
}) {
  const waiting = card.kind === 'awaiting_child'
  return (
    // Waiting is not needing: this card asks nothing, so it must not wear the you-are-the-blocker
    // frame.
    <div className={waiting
      ? 'rounded-lg border border-line bg-sunken px-3.5 py-3'
      : 'rounded-lg border border-warn/40 bg-warn/10 px-3.5 py-3'}>
      <div className="flex items-start gap-2.5">
        <div className="min-w-0 flex-1 space-y-2">
          {/* The card's TITLE, so it uses the shared heading and only the colour differs. */}
          <SectionHeader className={waiting ? 'text-muted' : 'text-warn'}>
            {waiting ? 'Waiting on a sub-item' : 'Need your attention'}
          </SectionHeader>
          {/* Peers in a numbered list, so one size and one weight: the `n label` column already
              carries the ordering. */}
          <Row n="1" label="Why">
            {/* Markdown, because raw text prints the deputy's bold labels as asterisks. `report`,
                not `chat`: the panel's own voice. */}
            {/* The variant is calibrated for a scrolling report, so the first and last block give
                their margins back. */}
            <div className="space-y-2 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0">
              <Markdown text={card.why} variant="report" tone="dev" />
              {card.detail && <Markdown text={card.detail} variant="report" tone="dev" />}
            </div>
          </Row>
          <Row n="2" label="What">
            <p className="text-[13px] leading-snug text-fg">{card.do}</p>
            {/* One button per act: the card says what to do, the bar is where you do it. */}
            {card.click === 'chat' && (
              <button onClick={() => onAct(card.click)} disabled={busy === card.click}
                      className="mt-1.5 inline-flex items-center gap-1.5 rounded-md bg-warn px-2.5 py-1 text-[13px] font-semibold text-white transition hover:brightness-110 disabled:opacity-50">
                {busy === card.click ? <Loader2 size={13} className="animate-spin" />
                                     : <ActionIcon id={card.click} />}
                Open chat
              </button>
            )}
          </Row>
          {/* The sub-items themselves, because an id alone is a join key, not an answer. */}
          {card.children.length > 0 && (
            <Row n="2" label="Blocked on">
              <ul className="space-y-1">
                {card.children.map((c) => (
                  <li key={c.id}>
                    <button
                      onClick={() => navigate({ name: 'item', repoId: contextId, itemId: c.id,
                                                tab: null, sub: null })}
                      className="group flex w-full items-baseline gap-2 rounded px-1 py-0.5 text-left
                                 transition hover:bg-hover">
                      <span className="shrink-0 font-mono text-[10px] text-faint">{c.id.slice(0, 8)}</span>
                      <span className="min-w-0 flex-1 truncate text-[13px] text-fg
                                       group-hover:text-accent-text">{c.title || '(untitled)'}</span>
                      <span className="shrink-0 text-[11px] text-muted">
                        {PHASE_LABEL[c.phase] ?? c.phase}
                        {c.status ? ` · ${STATUS_LABEL[c.status] ?? c.status}` : ''}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </Row>
          )}
          {card.basis.length > 0 && (
            <Row n="3" label="Reference">
              {/* No bullet glyph: the label column already separates these from the row above. */}
              <ul className="space-y-0.5 text-[13px] leading-snug text-fg">
                {card.basis.map((b, i) => <li key={i}>{b}</li>)}
              </ul>
            </Row>
          )}
          {card.questions.length > 0 && (
            <ol className="space-y-1.5 border-t border-warn/25 pt-2">
              {card.questions.map((q, i) => (
                <li key={i} className="text-[13px] leading-snug">
                  <span className="mr-1 font-mono text-[10px] text-warn">?{i + 1}</span>
                  <span className="text-fg">{q.question}</span>
                  {q.recommend && <div className="mt-0.5 text-[11px] text-muted">recommends: {q.recommend}</div>}
                  {q.why && <div className="text-[11px] text-faint">{q.why}</div>}
                  {q.instead && <div className="text-[11px] text-faint">instead: {q.instead}</div>}
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </div>
  )
}
