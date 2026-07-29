// The live-push client (`/api/ws/dashboard`) — the other half of routing-audit §7.6.
//
// It receives TOPICS, never values. A frame says "everything under `dev:my-repo:` changed" and this
// hands it to the cache, which refetches those keys over HTTP. So every number on screen still has
// exactly one source, and a pushed frame can never disagree with a polled read about a value — the
// failure mode that makes data-carrying push channels dangerous, and the reason this one is not.
//
// Two consequences that matter more than the socket itself:
//   * Changes appear when they happen, not on the next tick.
//   * While the socket is up the cache raises its polling to a slow backstop; when it drops, the
//     backstop is released and the ordinary cadence resumes. A dead channel degrades to exactly the
//     pre-push behaviour — never to a screen that has quietly stopped updating.

import { invalidate } from './index'
import { setPushOnline } from './store'

// Reconnect backoff. Starts fast (a daemon restart during development is the common case and should
// recover in about a second) and caps low enough that a longer outage still recovers unattended.
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
    // The hello is the handshake that matters: its arrival — not the socket opening — is what
    // proves the daemon is actually serving this channel, so that is when the backstop engages.
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
