import { useEffect, useState } from 'react'

// ── the app's ONE responsive rule ──
//
// Only the main surface holds the work, so the rails yield to it in a fixed order.
//
// A remembered rail width is a PREFERENCE, clamped to what the window can give.

export const NAV_W = 192 // the expanded nav rail
export const NAV_W_ICON = 56 // its icon strip
export const HANDLE_W = 8 // the chat rail's drag handle
export const CHAT_MIN = 360
export const CHAT_MAX = 900
export const MAIN_MIN = 560 // the narrowest a work surface stays worth rendering

// Each is the exact viewport width at which the two minimums stop fitting beside that nav rail.
export const NAV_COLLAPSE_AT = NAV_W + HANDLE_W + MAIN_MIN + CHAT_MIN
export const STACK_AT = NAV_W_ICON + HANDLE_W + MAIN_MIN + CHAT_MIN

export function useViewportWidth(): number {
  const [w, setW] = useState(() => (typeof window === 'undefined' ? 1440 : window.innerWidth))
  useEffect(() => {
    const onResize = () => setW(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  return w
}

export type Frame = {
  navIcons: boolean // the nav rail renders as its icon strip
  stacked: boolean // main and chat cannot share the row — one at a time
  railWidth: number // px for an open chat rail while the two share the row
}

// A manual collapse is honoured at every width, and the automatic one only ever adds to it.
export function useFrame(chatPref: number, navPref: boolean): Frame {
  const vw = useViewportWidth()
  const navIcons = navPref || vw < NAV_COLLAPSE_AT
  const room = vw - (navIcons ? NAV_W_ICON : NAV_W) - HANDLE_W
  return {
    navIcons,
    stacked: room < MAIN_MIN + CHAT_MIN,
    railWidth: Math.max(CHAT_MIN, Math.min(chatPref, room - MAIN_MIN)),
  }
}

// ── inside a pane ──
//
// Nothing adapts by growing a horizontal scrollbar, which hides content behind an invisible
// gesture. Panes REFLOW, and measure themselves rather than the window.

export const PANE = {
  narrow: 480, // below this a pane shows one column and its controls shed their labels
  mid: 720, // below this a pane stops trying to hold two full-width things side by side
}

// State plus an effect, not a ref: a ref sees the element once, missing a container that mounts
// later.
export function useContainerWidth<T extends HTMLElement>(): [(node: T | null) => void, number] {
  const [node, setNode] = useState<T | null>(null)
  const [w, setW] = useState(0)
  useEffect(() => {
    if (!node) return
    const measure = () => setW(node.getBoundingClientRect().width)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(node)
    window.addEventListener('resize', measure)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [node])
  return [setNode, w]
}

// A tab rail sheds LABELS first, but the active tab keeps its word — bare icons stop answering
// "where am I".
export const TAB_SEAT = 108

export function railTight(width: number, tabs: number, extra = 0): boolean {
  return width > 0 && width < tabs * TAB_SEAT + extra
}
