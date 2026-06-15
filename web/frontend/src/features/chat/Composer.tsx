import { useRef, useState } from 'react'
import { Send } from 'lucide-react'
import CommandPalette from './CommandPalette'

// The message input: a bordered card with a multi-line textarea + send, and the "/"
// command palette. Owns the palette's UI state (highlight, dismissed-with-Esc) and the
// keyboard handling; the message text itself is controlled by the parent.
export default function Composer({
  value,
  onChange,
  onSend,
  ready,
  busy,
  commands,
  ctxLabel,
}: {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  ready: boolean
  busy: boolean
  commands: string[]
  ctxLabel: string
}) {
  const [palIdx, setPalIdx] = useState(0)
  const [palHidden, setPalHidden] = useState(false) // dismissed with Esc until next edit
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  // Open while the input is a single "/token" (no space yet).
  const palMatch = value.match(/^\/(\S*)$/)
  const palQuery = palMatch ? palMatch[1].toLowerCase() : null
  const palItems = palQuery !== null ? commands.filter((c) => c.toLowerCase().includes(palQuery)).slice(0, 8) : []
  const palOpen = palQuery !== null && palItems.length > 0 && !palHidden

  function accept(name: string) {
    onChange(`/${name} `)
    setPalHidden(false)
    setPalIdx(0)
    inputRef.current?.focus()
  }

  return (
    <div className="shrink-0 p-3">
      <div className="relative">
        {palOpen && <CommandPalette items={palItems} activeIndex={palIdx} onHover={setPalIdx} onPick={accept} />}
        <div className="rounded-lg border border-line bg-sunken focus-within:border-accent">
          <textarea
            ref={inputRef}
            className="max-h-48 min-h-[4.5rem] w-full resize-none overflow-y-auto bg-transparent px-3 pt-2.5 text-sm leading-relaxed text-fg outline-none placeholder:text-faint disabled:opacity-50"
            rows={3}
            placeholder={ready ? `Message ${ctxLabel} SuperMe…` : 'connecting…'}
            value={value}
            disabled={!ready}
            onChange={(e) => {
              onChange(e.target.value)
              setPalHidden(false)
              setPalIdx(0)
            }}
            onKeyDown={(e) => {
              if (palOpen) {
                if (e.key === 'ArrowDown') {
                  e.preventDefault()
                  setPalIdx((i) => (i + 1) % palItems.length)
                  return
                }
                if (e.key === 'ArrowUp') {
                  e.preventDefault()
                  setPalIdx((i) => (i - 1 + palItems.length) % palItems.length)
                  return
                }
                if (e.key === 'Enter' || e.key === 'Tab') {
                  e.preventDefault()
                  accept(palItems[palIdx])
                  return
                }
                if (e.key === 'Escape') {
                  e.preventDefault()
                  setPalHidden(true)
                  return
                }
              }
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                onSend()
              }
            }}
          />
          <div className="flex items-center justify-between gap-2 px-2.5 pb-2 pt-1">
            <span className="truncate text-[10px] text-faint">/ commands · Enter to send · Shift+Enter for newline</span>
            <button
              className="flex shrink-0 items-center justify-center rounded-md bg-accent p-1.5 text-on-accent disabled:opacity-40"
              disabled={busy || !ready || !value.trim()}
              onClick={onSend}
              title="Send (Enter)"
              aria-label="Send"
            >
              <Send size={15} strokeWidth={2} />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
