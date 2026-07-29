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

// `/repo/<id>/item/<item>/pr` is its OWN page, not the cockpit with an overlay laid over it. Reading
// a diff wants the whole screen, and the owner wants the board and the item's chat still alive
// behind it — so the PR surface opens in a separate browser tab and this is where that tab forks.
// Mounted at the root, above App, so the PR tab carries NONE of the cockpit: no orbit poll, no
// attention poll, no chat socket. One view, its own fetches, nothing else running.
//
// It is a path now (slice 4) rather than `?repo=&pr=`, but the fork stays HERE rather than becoming
// a route inside the shell — being a path and being its own document are independent decisions, and
// §3.1 wants both.
const q = new URLSearchParams(window.location.search)
const legacyRepo = q.get('repo')
const legacyItem = q.get('pr')
if (legacyRepo && legacyItem) {
  // A PR tab parked before this change is still open somewhere. Rewrite it to the path form in
  // place — no history entry, and the same page renders either way, so the owner sees nothing.
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
