// All FE I/O goes to the same-origin /api surface (Vite proxies to the BFF → daemon).
// Two tiny helpers so each resource module isn't repeating fetch + status-check + parse.

export async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${url}: ${r.status}`)
  return r.json()
}

export async function sendJSON<T>(
  url: string,
  method: 'POST' | 'PUT' | 'DELETE',
  body?: unknown,
): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${url}: ${r.status}`)
  // DELETE and some PUTs return no body; tolerate that.
  const text = await r.text()
  return (text ? JSON.parse(text) : undefined) as T
}
