import { useCallback, useEffect, useRef, useState } from 'react'
import { agentSocketUrl, getPalette } from '@/lib/api'
import type { WsFrames, TurnFrame, ApprovalResponseFrame, TimelineFrame as GenTimelineFrame } from '@/lib/api/generated/ws'
import type { Approval, RunMeta } from '../types'
import { SEED_COMMANDS } from '../util'

// The WS frame contract is generated from the daemon's frame models (R6) — `npm run gen:ws`.
type OutboundFrame = WsFrames['outbound'] // daemon → client (discriminated on `type`)

// A live timeline frame (F2): one event from a WATCHED item's run (build/vet/other phase), pushed
// from the daemon's item broker independent of any turn this panel fired. (The generated union's
// `type` discriminant is optional, so we alias the frame interface directly rather than Extract.)
export type TimelineFrame = GenTimelineFrame

type Handlers = {
  onResult: (text: string, sessionId: string | null) => void
  onError: (message: string) => void
  onTimeline?: (frame: TimelineFrame) => void  // F2: a watched item's live run event
}

// Owns the chat WebSocket and everything that's turn-streaming state: the live assistant
// text, busy/elapsed, the current tool status + approval request, the cached slash
// commands, and the per-turn run metadata. Completed turns are handed back through
// `handlers` so the caller can append to the message list / claim a session id.
//
// Like useSessions, this assumes the ChatPanel remounts on context change, so the WS
// effect stays `[]` and `contextId` is captured once. `handlers` is read through a ref so
// changing callbacks never tears down the socket.
export function useAgentSocket(contextId: string, mode: 'core' | 'dev', handlers: Handlers) {
  const CMDS_KEY = `superme.commands.${contextId}.${mode}` // dev/core have different palettes
  const [ready, setReady] = useState(false)
  const [live, setLive] = useState('') // streaming assistant text this turn
  const [busy, setBusy] = useState(false)
  const [statusLabel, setStatusLabel] = useState<string | null>(null)
  const [elapsed, setElapsed] = useState(0) // seconds since the turn started
  const [approval, setApproval] = useState<Approval | null>(null)
  const [commands, setCommands] = useState<string[]>(SEED_COMMANDS)
  const [meta, setMeta] = useState<RunMeta | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const liveRef = useRef('')
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers

  // The "/" palette is computed server-side per (context, mode): mode-correct, fresh from disk
  // (publish/disable/delete reflect at once), internal skills filtered out. Restore the localStorage
  // copy for an instant first paint, then fetch the authoritative list. `refreshCommands` lets the
  // caller re-pull (e.g. when the palette opens) so it never lags a publish.
  const refreshCommands = useCallback(async () => {
    try {
      const { commands: cmds } = await getPalette(contextId, mode)
      const merged = [...new Set([...cmds, ...SEED_COMMANDS])]
      setCommands(merged)
      localStorage.setItem(CMDS_KEY, JSON.stringify(merged))
    } catch {
      /* keep whatever we have */
    }
  }, [contextId, mode, CMDS_KEY])

  useEffect(() => {
    try {
      const cached = JSON.parse(localStorage.getItem(CMDS_KEY) || '[]')
      if (Array.isArray(cached) && cached.length) setCommands([...new Set([...cached, ...SEED_COMMANDS])])
    } catch {
      /* ignore */
    }
    refreshCommands()
  }, [CMDS_KEY, refreshCommands])

  // Commit whatever the agent has said but not yet had turned into a bubble, and clear the live
  // buffer. The ONE place a streamed message becomes a permanent one — every exit from a turn
  // (next message, result, error, socket close) routes through here, so no path can drop text the
  // owner already read. `force` still fires the handler on empty text so a bare command reply can
  // carry its session claim.
  const flush = useCallback((sessionId: string | null, opts?: { force?: boolean }) => {
    const text = liveRef.current
    liveRef.current = ''
    setLive('')
    if (text.trim() || opts?.force) handlersRef.current.onResult(text, sessionId)
  }, [])

  useEffect(() => {
    const ws = new WebSocket(agentSocketUrl())
    wsRef.current = ws
    ws.onopen = () => setReady(true)
    ws.onclose = () => setReady(false)
    ws.onmessage = (ev) => {
      const f = JSON.parse(ev.data) as OutboundFrame
      switch (f.type) {
        case 'init':
          // The WS init carries the daemon's mode-blind cached list (includes internal skills + the
          // other mode's). The "/" palette is now served by /dev/palette (mode-correct + filtered),
          // so we ignore init for commands and let refreshCommands own the list.
          break
        case 'text_delta':
          // A `text_delta` is one WHOLE assistant message, not a token — and one turn can produce
          // several: the agent speaks, does more work (a tool call, or an async subagent that
          // notifies on completion), then speaks again. So a new delta means the PREVIOUS message
          // is finished: commit it as its own bubble right now rather than gluing the turn's
          // speeches into one blob and splitting them retroactively at `result`.
          flush(null)
          liveRef.current = f.text
          setLive(f.text)
          setStatusLabel(null)
          break
        case 'status':
          setStatusLabel(f.tool_name)
          break
        case 'approval_request':
          setApproval({ id: f.id, tool_name: f.tool_name, tool_input: f.tool_input })
          break
        case 'result': {
          // Commit the turn's last message. Everything before it was already committed at its own
          // boundary. Fall back to the Result's own text when nothing streamed — command replies
          // and error subtypes arrive as a bare Result with no preceding text_delta.
          if (!liveRef.current.trim() && f.text?.trim()) liveRef.current = f.text
          // The session claim rides this final flush: one claim per turn.
          flush(f.session_id ?? null, { force: true })
          setStatusLabel(null)
          setBusy(false)
          // Don't wipe the model/context readout on a command reply (no run metadata).
          if (f.model || f.ctx_pct != null) {
            setMeta({ model: f.model ?? null, pct: f.ctx_pct ?? null, window: f.context_window ?? null })
          }
          break
        }
        case 'error':
          // Keep what the agent already said. A turn that fails after speaking still spoke — the
          // words are on screen and in the run trail, so discarding them here would recreate the
          // same "it said it, then it vanished" defect through a different door.
          flush(null)
          setStatusLabel(null)
          setBusy(false)
          handlersRef.current.onError(f.message)
          break
        case 'timeline':
          // F2: a live event from a work-item this panel is WATCHING (a background build/vet/other
          // phase run). Handed to the timeline view to append — never touches the turn-streaming state.
          handlersRef.current.onTimeline?.(f)
          break
      }
    }
    return () => ws.close()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Live response timer: ticks every 100ms while a turn is in flight, so the user sees a
  // moving "thinking… 2.4s" rather than a static label.
  useEffect(() => {
    if (!busy) {
      setElapsed(0)
      return
    }
    const start = Date.now()
    const id = setInterval(() => setElapsed((Date.now() - start) / 1000), 100)
    return () => clearInterval(id)
  }, [busy])

  // Fire a turn. Returns false (no-op) if a turn is in flight or the socket isn't open.
  // `opts` carries the dev binding: mode="dev" + the work-item id (the item owns its thread,
  // so the daemon persists the session back onto it).
  function send(
    prompt: string,
    resume: string | null,
    opts?: {
      mode?: 'core' | 'dev'; workItemId?: string; model?: string | null; effort?: string | null
      kind?: string; subjectRunId?: number  // session-kind launch (v1: diagnosis + its subject run)
    },
  ): boolean {
    const ws = wsRef.current
    if (busy || !ws || ws.readyState !== WebSocket.OPEN) return false
    // model/effort are the SESSION runtime override (the composer picker) — sent per-turn; they never
    // persist a default. null = fall to the server precedence (work-item → repo → system). kind +
    // subject_run_id only matter at a session's BIRTH (the daemon stamps them write-once).
    const frame: TurnFrame = {
      type: 'turn',
      prompt,
      context_id: contextId,
      resume,
      model: opts?.model ?? null,
      effort: opts?.effort ?? null,
      mode: opts?.mode ?? 'core',
      work_item_id: opts?.workItemId ?? null,
      kind: opts?.kind ?? null,
      subject_run_id: opts?.subjectRunId ?? null,
    }
    ws.send(JSON.stringify(frame))
    setBusy(true)
    // Commit any orphan from a turn that ended without a result/error frame (a socket hiccup)
    // rather than clearing it — clearing loses it, and carrying it forward would attach the last
    // turn's words to this one.
    flush(null)
    return true
  }

  function answer(approved: boolean) {
    const ws = wsRef.current
    if (ws && approval) {
      const frame: ApprovalResponseFrame = { type: 'approval_response', id: approval.id, approved }
      ws.send(JSON.stringify(frame))
    }
    setApproval(null)
  }

  // F2: (un)subscribe this panel to a work-item's live event broker. `itemId=null` stops watching.
  // Independent of turns — passive observation, so it works while a background phase run is in flight.
  const watch = useCallback((itemId: string | null) => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'watch', item_id: itemId }))
    }
  }, [])

  // Clear streaming state when switching to / opening another conversation.
  function clearStream() {
    liveRef.current = ''
    setLive('')
    setStatusLabel(null)
  }
  function clearMeta() {
    setMeta(null)
  }
  // Seed the model·context% readout for a session we're OPENING (not a live turn) — e.g. from that
  // session's last recorded run — so the header shows its model persistently instead of "history".
  const seedMeta = useCallback((m: RunMeta | null) => setMeta(m), [])

  return { ready, live, busy, statusLabel, elapsed, approval, commands, meta, send, answer, watch, clearStream, clearMeta, seedMeta, refreshCommands }
}
