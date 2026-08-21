import { useEffect, useRef } from 'react'

// The one scroll rule for every message column: auto-scroll ONLY when the reader is already at the
// bottom.
//
// `preserve()` is for prepending older content: it banks the height and restores the viewport by
// the delta.
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
