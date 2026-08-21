// The live-push client. It receives TOPICS, never values, so every number keeps exactly one source.
//
// While the socket is up the cache raises its polling to a slow backstop; when it drops, the
// ordinary cadence resumes.

import { invalidate } from './index'
import { setPushOnline } from './store'

// Starts fast, because a daemon restart during development should recover in about a second, and
// caps low.
const BACKOFF_MS = [1000, 2000, 5000, 10000, 15000]

let socket: WebSocket | null = null
let attempt = 0
let retry: ReturnType<typeof setTimeout> | null = null
let stopped = false

function url(): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/api/ws/dashboard`
}

function connect() {
  if (stopped || socket) return
  let ws: WebSocket
  try {
    ws = new WebSocket(url())
  } catch {
    schedule()
    return
  }
  socket = ws

  ws.onmessage = (e) => {
    let frame: { type?: string; topics?: string[] }
    try {
      frame = JSON.parse(e.data)
    } catch {
      return
    }
    // The hello, not the socket opening, is what proves the daemon is serving this channel.
    if (frame.type === 'dashboard_hello') {
      attempt = 0
      setPushOnline(true)
      return
    }
    if (frame.type === 'invalidate' && frame.topics?.length) invalidate(...frame.topics)
  }

  const down = () => {
    if (socket === ws) socket = null
    setPushOnline(false) // release the backstop — polling carries the load again
    schedule()
  }
  ws.onclose = down
  ws.onerror = () => { try { ws.close() } catch { /* already closing */ } }
}

function schedule() {
  if (stopped || retry) return
  const wait = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]
  attempt += 1
  retry = setTimeout(() => { retry = null; connect() }, wait)
}

/** Open the channel and keep it open for the life of the tab. Idempotent. */
export function startPush(): () => void {
  stopped = false
  connect()
  return () => {
    stopped = true
    if (retry) { clearTimeout(retry); retry = null }
    setPushOnline(false)
    try { socket?.close() } catch { /* already gone */ }
    socket = null
  }
}
