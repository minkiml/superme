import { useEffect, useRef, useState } from 'react'
import {
  agentSocketUrl,
  listSessions,
  readSession,
  deleteSession,
  type SessionMeta,
} from '../api'
import Markdown from './Markdown'

type Msg = { role: 'you' | 'superme'; text: string }
type Approval = { id: string; tool_name: string; tool_input: any }

const CONTEXT = 'global'
const STORE_KEY = `superme.session.${CONTEXT}`
const CMDS_KEY = `superme.commands.${CONTEXT}`
// Seed so the "/" palette isn't empty before the first turn populates the real list.
// Includes our shared command (/model) + the headless built-ins.
const SEED_COMMANDS = ['model', 'compact', 'clear']

// 'claude-opus-4-7' -> 'opus 4.7'; falls back to the raw id.
function formatModel(id: string | null): string {
  if (!id) return ''
  const m = id.match(/claude-([a-z]+)-(\d+)-(\d+)/)
  return m ? `${m[1]} ${m[2]}.${m[3]}` : id
}

export default function ChatPanel() {
  const [messages, setMessages] = useState<Msg[]>([])
  const [live, setLive] = useState('') // streaming assistant text this turn
  const [statusLabel, setStatusLabel] = useState<string | null>(null)
  const [approval, setApproval] = useState<Approval | null>(null)
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState(false)
  const [input, setInput] = useState('')
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [elapsed, setElapsed] = useState(0) // seconds since the turn started (live)
  const [olderHidden, setOlderHidden] = useState(0) // # of replayed-but-skipped older bubbles
  const [meta, setMeta] = useState<{ model: string | null; pct: number | null; window: number | null } | null>(null)
  const [commands, setCommands] = useState<string[]>(SEED_COMMANDS)
  const [palIdx, setPalIdx] = useState(0)
  const [palHidden, setPalHidden] = useState(false) // dismissed with Esc until next edit
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const liveRef = useRef('')
  const sessionRef = useRef<string | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  // Keep the active session id in three places in sync: ref (for ws sends),
  // state (for the picker), and localStorage (for refresh-continuity).
  function setSession(id: string | null) {
    sessionRef.current = id
    setActiveId(id)
    if (id) localStorage.setItem(STORE_KEY, id)
    else localStorage.removeItem(STORE_KEY)
  }

  async function refreshSessions() {
    try {
      setSessions(await listSessions(CONTEXT))
    } catch {
      /* daemon may be down; leave the list as-is */
    }
  }

  // Replay a past session's bubbles (history lives in the SDK transcript).
  async function openSession(id: string) {
    setSession(id)
    setLive('')
    liveRef.current = ''
    setStatusLabel(null)
    try {
      const s = await readSession(id, CONTEXT)
      setMessages(s.messages)
      setOlderHidden(s.truncated ? s.total - s.messages.length : 0)
    } catch {
      setMessages([])
      setOlderHidden(0)
    }
  }

  function newChat() {
    setSession(null)
    setMessages([])
    setOlderHidden(0)
    setMeta(null)
    setLive('')
    liveRef.current = ''
    setStatusLabel(null)
    setInput('')
  }

  async function removeSession(id: string) {
    try {
      await deleteSession(id, CONTEXT)
    } catch {
      /* ignore */
    }
    if (sessionRef.current === id) newChat()
    refreshSessions()
  }

  // On mount: resume the stored session (replay its bubbles), load the session list,
  // and restore the cached slash-command palette.
  useEffect(() => {
    const stored = localStorage.getItem(STORE_KEY)
    if (stored) openSession(stored)
    refreshSessions()
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
          setMessages((m) => [...m, { role: 'superme', text: f.text }])
          liveRef.current = ''
          setLive('')
          setStatusLabel(null)
          setBusy(false)
          // A brand-new conversation just got its session id — claim & list it.
          const wasNew = !sessionRef.current
          if (f.session_id) {
            setSession(f.session_id)
            if (wasNew) refreshSessions()
          }
          // Don't wipe the model/context readout on a command reply (no run metadata).
          if (f.model || f.context_pct != null) {
            setMeta({ model: f.model ?? null, pct: f.context_pct ?? null, window: f.context_window ?? null })
          }
          break
        }
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight)
  }, [messages, live, statusLabel, approval])

  // Live response timer: ticks every 100ms while a turn is in flight, so the user
  // sees a moving "thinking… 2.4s" rather than a static label.
  useEffect(() => {
    if (!busy) {
      setElapsed(0)
      return
    }
    const start = Date.now()
    const id = setInterval(() => setElapsed((Date.now() - start) / 1000), 100)
    return () => clearInterval(id)
  }, [busy])

  function send() {
    const ws = wsRef.current
    if (!input.trim() || busy || !ws || ws.readyState !== WebSocket.OPEN) return
    setMessages((m) => [...m, { role: 'you', text: input }])
    ws.send(
      JSON.stringify({
        type: 'turn',
        prompt: input,
        context_id: CONTEXT,
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

  // "/" command palette: open while the input is a single "/token" (no space yet).
  const palMatch = input.match(/^\/(\S*)$/)
  const palQuery = palMatch ? palMatch[1].toLowerCase() : null
  const palItems =
    palQuery !== null
      ? commands.filter((c) => c.toLowerCase().includes(palQuery)).slice(0, 8)
      : []
  const palOpen = palQuery !== null && palItems.length > 0 && !palHidden

  function acceptPalette(name: string) {
    setInput(`/${name} `)
    setPalHidden(false)
    setPalIdx(0)
    inputRef.current?.focus()
  }

  return (
    <div className="flex h-full min-h-0 flex-col border-l border-slate-800">
      <div className="shrink-0 border-b border-slate-800 px-4 py-2">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold text-slate-200">Chat · global</span>
          <span className={`text-xs ${ready ? 'text-emerald-400' : 'text-slate-500'}`}>
            {ready ? '● connected' : '○ connecting'}
          </span>
        </div>
        <div className="mt-2 flex items-center gap-2">
          <select
            className="min-w-0 flex-1 truncate rounded bg-slate-950 px-2 py-1 text-xs text-slate-300 ring-1 ring-slate-700 disabled:opacity-50"
            value={activeId ?? ''}
            disabled={busy}
            onChange={(e) => (e.target.value ? openSession(e.target.value) : newChat())}
          >
            <option value="">{activeId ? '— switch session —' : 'New chat'}</option>
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title} · {s.message_count}
              </option>
            ))}
          </select>
          <button
            className="shrink-0 rounded bg-slate-700 px-2 py-1 text-xs text-slate-100 hover:bg-slate-600 disabled:opacity-50"
            disabled={busy}
            onClick={newChat}
            title="Start a new conversation"
          >
            ＋ New
          </button>
          {activeId && (
            <button
              className="shrink-0 rounded px-1.5 py-1 text-xs text-slate-500 hover:text-red-400 disabled:opacity-50"
              disabled={busy}
              onClick={() => removeSession(activeId)}
              title="Forget this session"
            >
              ✕
            </button>
          )}
        </div>
        {meta && (meta.model || meta.pct != null) && (
          <div className="mt-1.5 flex items-center gap-2 text-[11px] text-slate-500">
            {meta.model && <span className="text-slate-400">{formatModel(meta.model)}</span>}
            {meta.pct != null && meta.window && (
              <span title="Context window used by this conversation">
                · context {meta.pct}% of {Math.round(meta.window / 1000)}k
              </span>
            )}
          </div>
        )}
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-auto p-4">
        {messages.length === 0 && !live && (
          <div className="text-sm text-slate-500">Talk to global SuperMe. It reads/writes the knowledge on the left.</div>
        )}
        {olderHidden > 0 && (
          <div className="text-center text-xs text-slate-600">
            ⋯ {olderHidden} earlier message{olderHidden === 1 ? '' : 's'} hidden — SuperMe still has the full context
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === 'you' ? 'text-right' : 'text-left'}>
            <div
              className={`inline-block max-w-[90%] overflow-hidden break-words rounded-lg px-3 py-2 text-sm [overflow-wrap:anywhere] ${
                m.role === 'you'
                  ? 'whitespace-pre-wrap bg-sky-700 text-white'
                  : 'bg-slate-800 text-slate-200'
              }`}
            >
              {m.role === 'you' ? m.text : <Markdown text={m.text} />}
            </div>
          </div>
        ))}
        {live && (
          <div className="text-left">
            <div className="inline-block max-w-[90%] overflow-hidden break-words rounded-lg bg-slate-800 px-3 py-2 text-sm text-slate-200 [overflow-wrap:anywhere]">
              <Markdown text={live} />
            </div>
          </div>
        )}
        {busy && (
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <span className="flex gap-0.5">
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-sky-400 [animation-delay:-0.3s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-sky-400 [animation-delay:-0.15s]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-sky-400" />
            </span>
            <span>{statusLabel ? `${statusLabel}…` : live ? 'responding…' : 'thinking…'}</span>
            <span className="tabular-nums text-slate-500">{elapsed.toFixed(1)}s</span>
          </div>
        )}

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
        <div className="flex items-end gap-2">
          <div className="relative flex-1">
            {palOpen && (
              <div className="absolute bottom-full left-0 mb-1 w-full overflow-hidden rounded-lg border border-slate-700 bg-slate-900 shadow-lg">
                <div className="border-b border-slate-800 px-3 py-1 text-[10px] uppercase tracking-wide text-slate-500">
                  commands &amp; skills
                </div>
                {palItems.map((c, i) => (
                  <button
                    key={c}
                    className={`block w-full truncate px-3 py-1.5 text-left text-xs ${
                      i === palIdx ? 'bg-sky-700 text-white' : 'text-slate-300 hover:bg-slate-800'
                    }`}
                    onMouseEnter={() => setPalIdx(i)}
                    onClick={() => acceptPalette(c)}
                  >
                    /{c}
                  </button>
                ))}
              </div>
            )}
            <textarea
              ref={inputRef}
              className="max-h-48 min-h-[6rem] w-full resize-none overflow-y-auto rounded bg-slate-950 px-3 py-2 text-sm leading-relaxed text-slate-200 outline-none ring-1 ring-slate-700 disabled:opacity-50"
              rows={4}
              placeholder={ready ? 'Message global SuperMe…  ( / for commands · Enter to send · Shift+Enter newline )' : 'connecting…'}
              value={input}
              disabled={!ready}
              onChange={(e) => {
                setInput(e.target.value)
                setPalHidden(false)
                setPalIdx(0)
              }}
              onKeyDown={(e) => {
                if (palOpen) {
                  if (e.key === 'ArrowDown') {
                    e.preventDefault()
                    setPalIdx((i) => (i + 1) % palItems.length)
                    return
                  }
                  if (e.key === 'ArrowUp') {
                    e.preventDefault()
                    setPalIdx((i) => (i - 1 + palItems.length) % palItems.length)
                    return
                  }
                  if (e.key === 'Enter' || e.key === 'Tab') {
                    e.preventDefault()
                    acceptPalette(palItems[palIdx])
                    return
                  }
                  if (e.key === 'Escape') {
                    e.preventDefault()
                    setPalHidden(true)
                    return
                  }
                }
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  send()
                }
              }}
            />
          </div>
          <button
            className="shrink-0 rounded bg-sky-600 px-2.5 py-1.5 text-sm text-white disabled:opacity-40"
            disabled={busy || !ready || !input.trim()}
            onClick={send}
            title="Send (Enter)"
          >
            {busy ? '…' : '➤'}
          </button>
        </div>
      </div>
    </div>
  )
}
