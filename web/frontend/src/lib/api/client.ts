// All FE I/O goes to the same-origin `/api` surface. Two helpers, so each resource module is not
// repeating fetch, status-check and parse.

// The daemon writes genuinely useful failure messages into `detail` — a 409 explains the reason.
//
// Surface it verbatim as the Error message, so every catch site's banner shows the daemon's own
// words.
class ApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
    this.detail = detail
    this.name = 'ApiError'
  }
  // Catch sites stringify the error, so return the daemon's words alone rather than a class-name
  // prefix.
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
