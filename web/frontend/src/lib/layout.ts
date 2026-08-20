import { useCallback, useEffect, useRef, useState } from 'react'

// ── The app's ONE responsive rule ────────────────────────────────────────────────────────────
// Every page is the same three-band frame: the nav rail, the main surface, the chat rail. Only the
// main surface holds the work, so it is the band that must never be squeezed — the two rails yield
// to it, in a fixed order, at widths derived from the viewport rather than from device-class
// breakpoints. Three steps, and every surface inherits them by sitting in the frame:
//
//   1. The nav rail collapses to its icon strip first. It costs the least to lose — every row keeps
//      its icon and its tooltip, and its labels are the shortest text on screen.
//   2. The chat rail then narrows, down to CHAT_MIN.
//   3. Below the width where both still fit, they STACK: the chat rail shows as its own icon strip,
//      and opening it hands it the whole area instead of splitting an area too small to split. One
//      readable surface beats two unreadable ones.
//
// A remembered rail width is a PREFERENCE, not a promise. `railWidth` clamps the stored width to
// what the window can actually give, so a rail dragged wide on a large screen cannot swallow the
// board on a small one — which was the defect this exists to remove: the width was persisted in
// pixels and applied unconditionally, so a 725px rail on a 1000px window left the workspace 75px.

export const NAV_W = 192 // the expanded nav rail
export const NAV_W_ICON = 56 // its icon strip
export const HANDLE_W = 8 // the chat rail's drag handle
export const CHAT_MIN = 360
export const CHAT_MAX = 900
export const MAIN_MIN = 560 // the narrowest a work surface stays worth rendering

// The two thresholds, stated as what they protect rather than as round numbers: each is the exact
// viewport width at which MAIN_MIN + CHAT_MIN stops fitting beside that nav rail.
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

// `navPref` is the owner's own collapse choice: a manual collapse is honoured at every width, and
// the automatic one only ever adds to it — the layout may take space away, never hand it back
// against an explicit choice.
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

// ── Inside a pane ────────────────────────────────────────────────────────────────────────────
// The frame above decides how wide a pane IS; these decide what a pane does with the width it got.
// Two rules, and they are the same rule twice:
//
//   Nothing adapts by growing a horizontal scrollbar. A sideways scrollbar hides content behind a
//   gesture — the reader cannot see that there is more, and on a trackpad they find it by accident.
//   Panes REFLOW instead: lanes wrap, labels drop to icons, side-by-side becomes stacked. (Code and
//   diffs are the one exception, and they are not layout: a wrapped source line is a changed line.)
//
//   A pane measures ITSELF, never the window. `useContainerWidth` is the container query this
//   Tailwind version does not have — the same answer, from a ResizeObserver.

export const PANE = {
  narrow: 480, // below this a pane shows one column and its controls shed their labels
  mid: 720, // below this a pane stops trying to hold two full-width things side by side
}

// A CALLBACK ref, not a `useRef` + `useEffect` pair. The pair only ever looked at `ref.current`
// once, on mount — so a container that mounts LATER (the common case: a table that renders after
// its first fetch resolves, a pane behind a loading state) was never observed at all, and the
// width stayed 0. Every consumer reads 0 as "not measured yet" and falls back to the widest
// layout, which is how a narrow table ended up clipping its own right-hand columns instead of
// shedding them. A callback ref fires on every attach and detach, so late mounts are ordinary.
export function useContainerWidth<T extends HTMLElement>(): [(node: T | null) => void, number] {
  const [w, setW] = useState(0)
  const ro = useRef<ResizeObserver | null>(null)
  const attach = useCallback((node: T | null) => {
    ro.current?.disconnect()
    ro.current = null
    if (!node) return
    ro.current = new ResizeObserver(([e]) => setW(e.contentRect.width))
    ro.current.observe(node)
    setW(node.getBoundingClientRect().width)
  }, [])
  useEffect(() => () => ro.current?.disconnect(), [])
  return [attach, w]
}

// A tab rail sheds its LABELS before it sheds anything else, and the active tab keeps its word —
// a rail of undifferentiated icons no longer answers "where am I", which is half of what a rail is
// for. `TAB_SEAT` is roughly what one labelled tab occupies; `extra` is anything else riding the
// same row. Used by every tab rail in the app, so they all narrow the same way.
export const TAB_SEAT = 108

export function railTight(width: number, tabs: number, extra = 0): boolean {
  return width > 0 && width < tabs * TAB_SEAT + extra
}
