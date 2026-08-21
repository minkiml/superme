import { type EvalReport } from '@/lib/api'
import { PSection } from './bits'

// The evaluator's verdict on a candidate, section by section.

// The behavioural-eval verdict the forge_kit produced — evidence for the gate-2 decision.
const VERDICT_TINT: Record<string, string> = {
  pass: 'bg-success/15 text-success',
  warn: 'bg-warn/15 text-warn',
  fail: 'bg-danger/15 text-danger',
  skipped: 'bg-hover text-muted',
}

export function EvalReportView({ report }: { report: EvalReport }) {
  const verdict = (report.verdict ?? 'unknown').toLowerCase()
  const issues = report.issues ?? []
  const checks = report.checks ?? []
  const m = report.metrics
  const highs = issues.filter((i) => i.severity === 'high').length
  return (
    <div className="space-y-4">
      {/* Scannable header: verdict + per-lens scores + issue tally */}
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded px-2 py-0.5 font-mono text-[11px] uppercase ${VERDICT_TINT[verdict] ?? 'bg-hover text-muted'}`}>
          {verdict}
        </span>
        {checks.map((c, i) => (
          <span key={i} className="rounded bg-hover px-1.5 py-0.5 font-mono text-[11px] text-muted" title={c.note}>
            {c.name?.replace(/_/g, ' ')} <span className={`${(c.score ?? 0) >= 4 ? 'text-success' : (c.score ?? 0) <= 2 ? 'text-danger' : 'text-warn'}`}>{c.score ?? '–'}/5</span>
          </span>
        ))}
        <span className="font-mono text-[11px] text-faint">
          {issues.length === 0 ? 'no issues' : `${issues.length} issue${issues.length > 1 ? 's' : ''}${highs ? ` · ${highs} high` : ''}`}
        </span>
      </div>
      {/* The artifact's OWN run cost, measured by exercising it once on a synthetic task */}
      {m && (
        <div className="space-y-1.5 rounded-md border border-line bg-app px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-faint">
            {m.kind === 'overhead' ? 'Always-on cost' : 'Artifact run cost'}
          </div>
          {report.trial_task && m.kind !== 'overhead' && !m.error && (
            <div className="font-mono text-[10px] text-faint">
              <span className="text-muted">Eval on:</span> {report.trial_task}
            </div>
          )}
          <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-muted">
            {m.error ? (
              <span className="text-warn">trial did not complete — {m.error}</span>
            ) : m.kind === 'overhead' ? (
              <span>
                <span className="text-faint">~</span>
                {(m.tokens_per_turn ?? 0).toLocaleString()}
                <span className="text-faint"> tokens / turn</span>
              </span>
            ) : m.context_tokens != null || m.output_tokens != null ? (
              <>
                {m.context_tokens != null && <span><span className="text-faint">context</span> ~{m.context_tokens.toLocaleString()}</span>}
                {m.output_tokens != null && <span><span className="text-faint">output</span> {m.output_tokens.toLocaleString()}</span>}
                {m.duration_s != null && <span><span className="text-faint">time</span> {m.duration_s}s</span>}
              </>
            ) : (
              /* legacy proposals (pre-footprint): cumulative token total only */
              <>
                {m.tokens != null && <span><span className="text-faint">tokens</span> {m.tokens.toLocaleString()}</span>}
                {m.duration_s != null && <span><span className="text-faint">time</span> {m.duration_s}s</span>}
              </>
            )}
          </div>
          {m.capped && !m.error && m.kind === 'run' && (
            <div className="font-mono text-[10px] text-warn">
              Floor only. Trial hit the turn cap ({m.capped}), so a full run costs more.
            </div>
          )}
        </div>
      )}
      {report.summary && (Array.isArray(report.summary) ? report.summary.length > 0 : true) && (
        <PSection title="Assessment">
          {Array.isArray(report.summary) ? (
            <ul className="space-y-1">
              {report.summary.map((b, i) => (
                <li key={i} className="flex gap-1.5 text-[13px] leading-snug text-fg">
                  <span className="text-faint">•</span>
                  <span>{b}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[13px] leading-relaxed text-fg">{report.summary}</p>
          )}
        </PSection>
      )}
      {issues.length > 0 && (
        <PSection title="Issues to fix before publishing">
          <ul className="space-y-2">
            {[...issues].sort((a, b) => (a.severity === 'high' ? -1 : 1) - (b.severity === 'high' ? -1 : 1)).map((it, i) => (
              <li key={i} className="rounded-md border border-line bg-app px-2.5 py-2 text-[12px]">
                <div className="flex items-start gap-1.5">
                  <span className={`mt-px rounded px-1 text-[9px] uppercase ${it.severity === 'high' ? 'bg-danger/15 text-danger' : 'bg-hover text-muted'}`}>
                    {it.severity ?? '?'}
                  </span>
                  <span className="text-fg">{it.what}</span>
                </div>
                {it.fix && <div className="mt-1 pl-1 text-faint">→ {it.fix}</div>}
              </li>
            ))}
          </ul>
        </PSection>
      )}
    </div>
  )
}
