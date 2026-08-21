import { useCallback, useEffect, useRef, useState } from 'react'
import { agentSocketUrl, getPalette } from '@/lib/api'
import type { WsFrames, TurnFrame, ApprovalResponseFrame, TimelineFrame as GenTimelineFrame } from '@/lib/api/generated/ws'
import type { Approval, RunMeta } from '../types'
import { SEED_COMMANDS } from '../util'

// The WS frame contract is generated from the daemon's frame models — `npm run gen:ws`.
type OutboundFrame = WsFrames['outbound'] // daemon → client (discriminated on `type`)

// One event from a WATCHED item's run, pushed from the daemon's broker independent of any turn this
// panel fired.
export type TimelineFrame = GenTimelineFrame

type Handlers = {
  onResult: (text: string, sessionId: string | null) => void
  onError: (message: string) => void
  onTimeline?: (frame: TimelineFrame) => void  // A watched item's live run event
}

// Owns the chat WebSocket and every piece of turn-streaming state, handing completed turns back
// through `handlers`.
//
// The panel remounts on context change, and `handlers` is read through a ref, so changing callbacks
// never tear down the socket.
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
  // A ref too: the handlers are installed once, so they close over the FIRST `busy`.
  const busyRef = useRef(false)
  const markBusy = useCallback((v: boolean) => {
    busyRef.current = v
    setBusy(v)
  }, [])
  // What `flush` most recently turned into a permanent bubble — see the dedupe in `text_delta`.
  const committedRef = useRef('')
  // Set while WE are closing: only an UNEXPECTED close means the turn was cut.
  const closingRef = useRef(false)

  // Restore the stored copy for an instant first paint, then fetch the authoritative list.
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

  // The ONE place a streamed message becomes permanent: every exit from a turn routes through here.
  const flush = useCallback((sessionId: string | null, opts?: { force?: boolean }) => {
    const text = liveRef.current
    liveRef.current = ''
    setLive('')
    if (text.trim() || opts?.force) {
      committedRef.current = text.trim()
      handlersRef.current.onResult(text, sessionId)
    }
  }, [])

  useEffect(() => {
    // Reset per socket: the ref outlives the effect, so a teardown would latch it on for the next.
    closingRef.current = false
    const ws = new WebSocket(agentSocketUrl())
    wsRef.current = ws
    ws.onopen = () => setReady(true)
    ws.onclose = () => {
      setReady(false)
      // A socket that dies mid-turn ENDS it: clearing `ready` alone left `busy` set forever.
      if (closingRef.current || !busyRef.current) return
      flush(null)
      markBusy(false)
      setStatusLabel(null)
      setApproval(null)   // a request nobody can answer any more
      handlersRef.current.onError('The connection dropped mid-turn — this run was cut short.')
    }
    ws.onmessage = (ev) => {
      const f = JSON.parse(ev.data) as OutboundFrame
      switch (f.type) {
        case 'init':
          // The init list is mode-blind, so it is ignored and the palette route owns the commands.
          break
        case 'text_delta':
          // A delta is one WHOLE message, and a turn can produce several, so a new one ends the
          // previous.
          flush(null)
          // Unless this delta IS the message just committed: the rail would draw the same sentence
          // twice.
          if (f.text.trim() && f.text.trim() === committedRef.current) break
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
          // Commit the turn's last message. Fall back to the Result's own text, since a command
          // reply streams nothing.
          if (!liveRef.current.trim() && f.text?.trim()) liveRef.current = f.text
          // The session claim rides this final flush: one claim per turn.
          flush(f.session_id ?? null, { force: true })
          setStatusLabel(null)
          markBusy(false)
          // Don't wipe the model/context readout on a command reply (no run metadata).
          if (f.model || f.ctx_pct != null) {
            setMeta({ model: f.model ?? null, pct: f.ctx_pct ?? null, window: f.context_window ?? null })
          }
          break
        }
        case 'error':
          // A turn that fails after speaking still spoke, so the words stay on screen and in the
          // trail.
          flush(null)
          setStatusLabel(null)
          markBusy(false)
          handlersRef.current.onError(f.message)
          break
        case 'timeline':
          // A live event from a WATCHED item, handed to the timeline; it never touches
          // turn-streaming state.
          handlersRef.current.onTimeline?.(f)
          break
      }
    }
    return () => {
      closingRef.current = true
      ws.close()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Ticks while a turn is in flight, so the user sees a moving "thinking…" rather than a static
  // label.
  useEffect(() => {
    if (!busy) {
      setElapsed(0)
      return
    }
    const start = Date.now()
    const id = setInterval(() => setElapsed((Date.now() - start) / 1000), 100)
    return () => clearInterval(id)
  }, [busy])

  // Returns false when a turn is in flight or the socket is closed. `opts` carries the dev binding.
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
    // The SESSION runtime override, sent per turn; they never persist a default.
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
    markBusy(true)
    // Commit an orphan from a turn that ended without a result frame: clearing loses it, carrying
    // it misattributes it.
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

  // Subscribe this panel to an item's live broker. Passive, so it works while a background run is
  // in flight.
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
  // Seed the readout for a session being OPENED, so the header shows its model rather than
  // "history".
  const seedMeta = useCallback((m: RunMeta | null) => setMeta(m), [])

  return { ready, live, busy, statusLabel, elapsed, approval, commands, meta, send, answer, watch, clearStream, clearMeta, seedMeta, refreshCommands }
}
