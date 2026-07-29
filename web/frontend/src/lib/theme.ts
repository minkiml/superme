// Light/dark theme switch. The `.dark` class on <html> selects the dark token set
// (index.css); absence = light. Persisted in localStorage, applied before render to
// avoid a flash.
export type Theme = 'light' | 'dark'
const KEY = 'superme.theme'

export function getTheme(): Theme {
  const s = localStorage.getItem(KEY)
  if (s === 'light' || s === 'dark') return s
  return 'dark' // default to the (now warm) dark
}

function applyTheme(t: Theme) {
  document.documentElement.classList.toggle('dark', t === 'dark')
}

export function setTheme(t: Theme) {
  localStorage.setItem(KEY, t)
  applyTheme(t)
}

export function initTheme(): Theme {
  const t = getTheme()
  applyTheme(t)
  return t
}
