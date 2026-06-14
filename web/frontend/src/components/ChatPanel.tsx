import { useEffect, useRef, useState } from 'react'
import { agentSocketUrl } from '../api'

type Msg = { role: 'you' | 'superme'; text: string }
type Approval = { id: string; tool_name: string; tool_input: any }

export default function ChatPanel() {
  const [messages, setMessages] = useState<Msg[]>([])
  const [live, setLive] = useState('') // streaming assistant text this turn
  const [statusLabel, setStatusLabel] = useState<string | null>(null)
  const [approval, setApproval] = useState<Approval | null>(null)
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState(false)
  const [input, setInput] = useState('')

  const wsRef = useRef<WebSocket | null>(null)
  const liveRef = useRef('')
  const sessionRef = useRef<string | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const ws = new WebSocket(agentSocketUrl())
    wsRef.current = ws
    ws.onopen = () => setReady(true)
    ws.onclose = () => setReady(false)
    ws.onmessage = (ev) => {
      const f = JSON.parse(ev.data)
      switch (f.type) {
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
        case 'result':
          setMessages((m) => [...m, { role: 'superme', text: f.text }])
          liveRef.current = ''
          setLive('')
          setStatusLabel(null)
          setBusy(false)
          if (f.session_id) sessionRef.current = f.session_id
          break
        case 'error':
          setMessages((m) => [...m, { role: 'superme', text: '⚠ ' + f.message }])
          liveRef.current = ''
          setLive('')
          setStatusLabel(null)
          setBusy(false)
          break
      }
    }
    return () => ws.close()
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight)
  }, [messages, live, statusLabel, approval])

  function send() {
    const ws = wsRef.current
    if (!input.trim() || busy || !ws || ws.readyState !== WebSocket.OPEN) return
    setMessages((m) => [...m, { role: 'you', text: input }])
    ws.send(
      JSON.stringify({
        type: 'turn',
        prompt: input,
        context_id: 'global',
        resume: sessionRef.current,
        model: null,
      }),
    )
    setInput('')
    setBusy(true)
    liveRef.current = ''
    setLive('')
  }

  function answer(approved: boolean) {
    const ws = wsRef.current
    if (ws && approval) ws.send(JSON.stringify({ type: 'approval_response', id: approval.id, approved }))
    setApproval(null)
  }

  return (
    <div className="flex h-full min-h-0 flex-col border-l border-slate-800">
      <div className="flex shrink-0 items-center justify-between border-b border-slate-800 px-4 py-2">
        <span className="text-sm font-semibold text-slate-200">Chat · global</span>
        <span className={`text-xs ${ready ? 'text-emerald-400' : 'text-slate-500'}`}>
          {ready ? '● connected' : '○ connecting'}
        </span>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-auto p-4">
        {messages.length === 0 && !live && (
          <div className="text-sm text-slate-500">Talk to global SuperMe. It reads/writes the knowledge on the left.</div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === 'you' ? 'text-right' : 'text-left'}>
            <div
              className={`inline-block max-w-[90%] overflow-hidden whitespace-pre-wrap break-words rounded-lg px-3 py-2 text-sm [overflow-wrap:anywhere] ${
                m.role === 'you' ? 'bg-sky-700 text-white' : 'bg-slate-800 text-slate-200'
              }`}
            >
              {m.text}
            </div>
          </div>
        ))}
        {live && (
          <div className="text-left">
            <div className="inline-block max-w-[90%] overflow-hidden whitespace-pre-wrap break-words rounded-lg bg-slate-800 px-3 py-2 text-sm text-slate-200 [overflow-wrap:anywhere]">
              {live}
            </div>
          </div>
        )}
        {statusLabel && <div className="text-xs italic text-slate-500">· {statusLabel}…</div>}

        {approval && (
          <div className="rounded-lg border border-amber-600 bg-amber-950/40 p-3 text-sm">
            <div className="mb-2 text-amber-200">
              Approve <span className="font-semibold">{approval.tool_name}</span>?
            </div>
            <pre className="mb-2 max-h-32 overflow-auto rounded bg-slate-950 p-2 text-xs text-slate-300">
              {JSON.stringify(approval.tool_input, null, 2)}
            </pre>
            <div className="flex gap-2">
              <button className="rounded bg-emerald-600 px-3 py-1 text-white" onClick={() => answer(true)}>
                Allow
              </button>
              <button className="rounded bg-red-600 px-3 py-1 text-white" onClick={() => answer(false)}>
                Deny
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="shrink-0 border-t border-slate-800 p-3">
        <div className="flex gap-2">
          <input
            className="flex-1 rounded bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none ring-1 ring-slate-700 disabled:opacity-50"
            placeholder={ready ? 'Message global SuperMe…' : 'connecting…'}
            value={input}
            disabled={!ready}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send()}
          />
          <button
            className="rounded bg-sky-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
            disabled={busy || !ready || !input.trim()}
            onClick={send}
          >
            {busy ? '…' : 'Send'}
          </button>
        </div>
      </div>
    </div>
  )
}
