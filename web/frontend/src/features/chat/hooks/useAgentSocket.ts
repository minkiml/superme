import { useEffect, useRef, useState } from 'react'
import { agentSocketUrl } from '@/lib/api'
import type { Approval, RunMeta } from '../types'
import { SEED_COMMANDS } from '../util'

type Handlers = {
  onResult: (text: string, sessionId: string | null) => void
  onError: (message: string) => void
}

// Owns the chat WebSocket and everything that's turn-streaming state: the live assistant
// text, busy/elapsed, the current tool status + approval request, the cached slash
// commands, and the per-turn run metadata. Completed turns are handed back through
// `handlers` so the caller can append to the message list / claim a session id.
//
// Like useSessions, this assumes the ChatPanel remounts on context change, so the WS
// effect stays `[]` and `contextId` is captured once. `handlers` is read through a ref so
// changing callbacks never tears down the socket.
export function useAgentSocket(contextId: string, handlers: Handlers) {
  const CMDS_KEY = `superme.commands.${contextId}`
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

  // Restore the cached "/" palette before the first turn populates the real list.
  useEffect(() => {
    try {
      const cached = JSON.parse(localStorage.getItem(CMDS_KEY) || '[]')
      if (Array.isArray(cached) && cached.length) setCommands([...new Set([...cached, ...SEED_COMMANDS])])
    } catch {
      /* ignore */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const ws = new WebSocket(agentSocketUrl())
    wsRef.current = ws
    ws.onopen = () => setReady(true)
    ws.onclose = () => setReady(false)
    ws.onmessage = (ev) => {
      const f = JSON.parse(ev.data)
      switch (f.type) {
        case 'init':
          if (Array.isArray(f.slash_commands) && f.slash_commands.length) {
            const cmds = [...new Set([...f.slash_commands, ...SEED_COMMANDS])]
            setCommands(cmds)
            localStorage.setItem(CMDS_KEY, JSON.stringify(cmds))
          }
          break
        case 'text_delta':
          liveRef.current += f.text
          setLive(liveRef.current)
          setStatusLabel(null)
          break
        case 'status':
          setStatusLabel(f.tool_name)
          break
        case 'approval_request':
          setApproval({ id: f.id, tool_name: f.tool_name, tool_input: f.tool_input })
          break
        case 'result': {
          liveRef.current = ''
          setLive('')
          setStatusLabel(null)
          setBusy(false)
          // Don't wipe the model/context readout on a command reply (no run metadata).
          if (f.model || f.context_pct != null) {
            setMeta({ model: f.model ?? null, pct: f.context_pct ?? null, window: f.context_window ?? null })
          }
          handlersRef.current.onResult(f.text, f.session_id ?? null)
          break
        }
        case 'error':
          liveRef.current = ''
          setLive('')
          setStatusLabel(null)
          setBusy(false)
          handlersRef.current.onError(f.message)
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
  function send(prompt: string, resume: string | null): boolean {
    const ws = wsRef.current
    if (busy || !ws || ws.readyState !== WebSocket.OPEN) return false
    ws.send(JSON.stringify({ type: 'turn', prompt, context_id: contextId, resume, model: null }))
    setBusy(true)
    liveRef.current = ''
    setLive('')
    return true
  }

  function answer(approved: boolean) {
    const ws = wsRef.current
    if (ws && approval) ws.send(JSON.stringify({ type: 'approval_response', id: approval.id, approved }))
    setApproval(null)
  }

  // Clear streaming state when switching to / opening another conversation.
  function clearStream() {
    liveRef.current = ''
    setLive('')
    setStatusLabel(null)
  }
  function clearMeta() {
    setMeta(null)
  }

  return { ready, live, busy, statusLabel, elapsed, approval, commands, meta, send, answer, clearStream, clearMeta }
}
