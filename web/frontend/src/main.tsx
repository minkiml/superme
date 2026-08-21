import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import PrPage from '@/features/dev/PrPage'
import '@fontsource-variable/inter'
import '@fontsource-variable/hanken-grotesk'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'
import '@fontsource/ibm-plex-mono/600.css'
import './index.css'
import { initTheme } from '@/lib/theme'
import { parse } from '@/lib/router'

initTheme()

// The PR route is its OWN page, not the cockpit with an overlay over it: a diff wants the whole
// screen.
//
// Mounted above App, so the PR tab carries no polls and no chat socket.
const q = new URLSearchParams(window.location.search)
const legacyRepo = q.get('repo')
const legacyItem = q.get('pr')
if (legacyRepo && legacyItem) {
  // Rewrite an older PR address in place — no history entry, and the same page renders either way.
  window.history.replaceState(
    null, '',
    `/repo/${encodeURIComponent(legacyRepo)}/item/${encodeURIComponent(legacyItem)}/pr`,
  )
}

const route = parse(window.location.pathname)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {route.name === 'pr' ? <PrPage itemId={route.itemId} contextId={route.repoId} /> : <App />}
  </React.StrictMode>,
)
