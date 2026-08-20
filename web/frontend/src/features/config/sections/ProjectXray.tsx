import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, ExternalLink, Play } from 'lucide-react'
import { runPromptExtraction, getPromptExtractionStatus, type PromptExtractionStatus } from '@/lib/api/dev'
import { fmtLocal } from '@/lib/format'
import { PHASE_LABEL, Empty } from '@/features/dev/common'
import { PaneHead } from '../controls'

// Prompt X-ray — inspect the REAL input prompts SuperMe sends over a work-item's lifecycle. Rather
// than capturing every run forever, this fires ONE throwaway probe on demand: a disposable work-item
// that runs the real triage→plan→build→vet→review→close pipeline unattended, captures each phase's
// actual input, then tears itself down (no merge, no anchor writes, no leftover) — leaving only the
// tagged run trace + these captured input pages. Each link opens the full input in a new tab.

export default function ProjectXray({ contextId, repoLabel }: { contextId: string; repoLabel: string }) {
  const [st, setSt] = useState<PromptExtractionStatus | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [launching, setLaunching] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(() => {
    getPromptExtractionStatus(contextId)
      .then((d) => { setSt(d); setErr(null) })
      .catch((e) => setErr(String(e)))
  }, [contextId])

  // Poll while a probe is in flight (the pipeline self-drives to close in the background); stop once
  // it's done. Cleared on unmount / context change.
  useEffect(() => {
    load()
    return () => { if (timer.current) clearTimeout(timer.current) }
  }, [load])
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current)
    if (st?.running) timer.current = setTimeout(load, 4000)
    return () => { if (timer.current) clearTimeout(timer.current) }
  }, [st, load])

  const fire = () => {
    setLaunching(true)
    setErr(null)
    runPromptExtraction(contextId)
      .then((d) => setSt(d))
      .catch((e) => setErr(String(e)))
      .finally(() => setLaunching(false))
  }

  const running = !!st?.running
  const links = st?.links ?? []

  return (
    <>
      <PaneHead
        title="Prompt X-ray"
        lede="Firing a probe spins up a throwaway work-item that runs the real pipeline (triage → plan → build → vet → review → close) on autopilot, captures each phase's input, then destroys itself — nothing is merged and no knowledge is written. Only the captured input pages and the run trace remain."
      />

      <div className="mb-5 flex items-center gap-3">
        <button
          onClick={fire}
          disabled={running || launching}
          title="Fire a throwaway probe that runs a full lifecycle and captures each phase's input"
          className="inline-flex items-center gap-1 rounded-md border border-accent/40 bg-accent/10 px-2 py-1 text-[12px] text-accent-text transition hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {launching || running ? (
            <><Loader2 size={13} className="animate-spin" /> Probe running…</>
          ) : (
            <><Play size={13} /> Capture a live run</>
          )}
        </button>
        {running ? (
          <span className="text-[12px] text-muted">
            agent running — the pipeline is self-driving to close; links appear per phase
          </span>
        ) : st?.finished_at ? (
          <span className="text-[12px] text-faint">last probe finished {fmtLocal(st.finished_at)}</span>
        ) : null}
      </div>

      {err && <div className="mb-4 rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-[12px] text-danger">{err}</div>}

      <div className="mb-2 text-[12px] font-semibold uppercase tracking-wider text-muted">
        Captured inputs {links.length > 0 && <span className="text-faint">· {links.length}</span>}
      </div>
      {links.length === 0 ? (
        <Empty>{running ? 'Waiting for the first phase to run…' : 'No probe captured yet — fire one above.'}</Empty>
      ) : (
        <div className="overflow-hidden rounded-lg border border-line">
          {links.map((l, i) => (
            <a
              key={l.run_id}
              href={l.url} target="_blank" rel="noreferrer"
              className="group flex items-center gap-3 border-b border-line bg-surface px-3.5 py-2.5 text-[13px] transition last:border-b-0 hover:bg-hover"
            >
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-dev/15 text-[10px] font-semibold text-dev">{i + 1}</span>
              <span className="w-16 shrink-0 font-medium text-fg">{PHASE_LABEL[l.phase ?? ''] ?? l.phase ?? 'run'}</span>
              <span className="w-[72px] shrink-0 tabular-nums text-faint">run #{l.run_id}</span>
              {l.started_at && <span className="tabular-nums text-faint">· {fmtLocal(l.started_at)}</span>}
              <ExternalLink size={13} className="ml-auto text-faint transition group-hover:text-dev" />
            </a>
          ))}
        </div>
      )}
    </>
  )
}
