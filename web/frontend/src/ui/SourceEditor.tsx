import { useLayoutEffect, useRef, useState } from 'react'

// The raw-markdown editor behind every Edit button.
//
// These files are hand-wrapped at a fixed column, so FIT THE COLUMN, not the text: scale the font
// until that column occupies the width available.

/** The column these files are hand-wrapped at. Fitting it is the whole trick. */
const TARGET_COLS = 100
// Below the floor the text stops being readable, so a very narrow frame wraps instead; above it, a
// wide modal would dwarf the chrome.
const MIN_PX = 9.5
const MAX_PX = 13.5

export default function SourceEditor({
  value, onChange, tone = 'accent', surface = 'bg-surface', className = '',
}: {
  value: string
  onChange: (next: string) => void
  /** Focus-ring colour — matches the surrounding surface's accent (`dev` inside the dev tabs). */
  tone?: 'accent' | 'dev'
  /** Background token, so the editor sits on the same plane as the panel that owns it. */
  surface?: string
  className?: string
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  const [fontPx, setFontPx] = useState<number | null>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const fit = () => {
      const cs = getComputedStyle(el)
      // Measured in THIS textarea's own font, so the ratio is exact for whatever monospace stack
      // resolved.
      const probe = document.createElement('span')
      probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;font-size:100px'
      probe.style.fontFamily = cs.fontFamily
      probe.textContent = '0'.repeat(TARGET_COLS)
      document.body.appendChild(probe)
      const widthAt100px = probe.getBoundingClientRect().width
      probe.remove()
      if (!widthAt100px) return
      const avail = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)
      setFontPx(Math.max(MIN_PX, Math.min(MAX_PX, (avail / widthAt100px) * 100)))
    }
    fit()
    // Font size never changes the element's width (it is 100%), so this cannot feed back on itself.
    const ro = new ResizeObserver(fit)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  return (
    <textarea
      ref={ref}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      spellCheck={false}
      // One frame of slightly-wrong text beats one frame of none, so fall back until the
      // measurement lands.
      style={{ fontSize: `${fontPx ?? 12.5}px` }}
      className={`h-[60vh] w-full resize-none overflow-y-auto whitespace-pre-wrap break-words
        rounded-md border border-line ${surface} p-3 font-mono leading-relaxed text-fg outline-none
        ${tone === 'dev' ? 'focus:border-dev' : 'focus:border-accent'} ${className}`}
    />
  )
}
