import { useEffect, useRef } from 'react'

// The one scroll rule for every message column in the app (chat rail · work-item timeline).
//
// Auto-scroll ONLY when the reader is already at the bottom. If they scrolled up to read history,
// arriving content must not yank them back down — during a live run that made earlier messages
// unreadable, because a frame lands every few seconds. `stick` tracks "near the bottom", updated on
// every user scroll; it starts true, so a freshly opened column opens pinned to the newest.
//
// `preserve()` is for prepending older content ("See more"): it banks the pre-load height, and the
// next effect restores the viewport by the delta instead of jumping anywhere.
export function useStickyScroll(deps: unknown[]) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const stickRef = useRef(true)
  const preserveRef = useRef<number | null>(null)

  function onScroll() {
    const el = scrollRef.current
    if (!el) return
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  function preserve() {
    preserveRef.current = scrollRef.current?.scrollHeight ?? null
  }

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    if (preserveRef.current != null) {
      el.scrollTop = el.scrollHeight - preserveRef.current
      preserveRef.current = null
    } else if (stickRef.current) {
      el.scrollTo(0, el.scrollHeight)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return { scrollRef, onScroll, preserve }
}
