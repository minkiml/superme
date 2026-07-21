// All FE I/O goes to the same-origin /api surface (Vite proxies to the BFF → daemon).
// Two tiny helpers so each resource module isn't repeating fetch + status-check + parse.

// The daemon writes genuinely useful failure messages into FastAPI's `detail` (a 409 explains the
// merge/gate/plan reason, a runbook, the retry shape). Swallowing it for a bare status left the
// owner staring at "409" (P7). Read the body and surface `detail` verbatim as the Error message, so
// every catch site's banner shows the daemon's own words; fall back to the status when there's none.
class ApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
    this.detail = detail
    this.name = 'ApiError'
  }
  // Catch sites render errors with `String(e)` / `${e}` → without this they'd read "ApiError: …".
  // Return the daemon's words alone so the banner is exactly the detail.
  toString() { return this.detail }
}

async function errorFrom(r: Response, url: string): Promise<ApiError> {
  let detail = ''
  try {
    const text = await r.text()
    if (text) {
      const parsed = JSON.parse(text)
      detail = typeof parsed?.detail === 'string' ? parsed.detail
        : Array.isArray(parsed?.detail) ? parsed.detail.map((d: { msg?: string }) => d?.msg).filter(Boolean).join('; ')
        : ''
    }
  } catch {
    /* non-JSON body — fall through to the status message */
  }
  return new ApiError(r.status, detail || `${url}: ${r.status}`)
}

export async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw await errorFrom(r, url)
  return r.json()
}

export async function sendJSON<T>(
  url: string,
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  body?: unknown,
): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!r.ok) throw await errorFrom(r, url)
  // DELETE and some PUTs return no body; tolerate that.
  const text = await r.text()
  return (text ? JSON.parse(text) : undefined) as T
}
