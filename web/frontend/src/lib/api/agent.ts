// The agent chat runs over a WebSocket (proxied through /api by Vite → BFF → daemon).
export function agentSocketUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/api/ws/agent`
}
