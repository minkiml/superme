import { useRef, useState } from 'react'
import { Send } from 'lucide-react'
import CommandPalette from './CommandPalette'
import ModelPicker from './ModelPicker'

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
  onPaletteOpen,
  modelOverride,
  effortOverride,
  onSelectModel,
  locked = null,
}: {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  ready: boolean
  busy: boolean
  commands: string[]
  ctxLabel: string
  onPaletteOpen?: () => void
  modelOverride?: string | null // the context's current model selection (for the picker's check)
  effortOverride?: string | null // the context's current effort selection (for the picker's check)
  onSelectModel?: (model: string, effort: string) => void // sends `/model <model> <effort>` directly
  // Greyed during autonomous phases: there is no live intake worker to receive a message.
  locked?: { reason: string } | null
}) {
  const [palIdx, setPalIdx] = useState(0)
  const [palHidden, setPalHidden] = useState(false) // dismissed with Esc until next edit
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  // Once the input resolves to exactly the model command, swap the generic palette for the picker.
  const modelOpen = !!onSelectModel && /^\/model\s*$/.test(value) && !palHidden

  // Open while the input is a single "/token" (no space yet).
  const palMatch = value.match(/^\/(\S*)$/)
  const palQuery = palMatch ? palMatch[1].toLowerCase() : null
  const palItems = palQuery !== null ? commands.filter((c) => c.toLowerCase().includes(palQuery)).slice(0, 8) : []
  const palOpen = !modelOpen && palQuery !== null && palItems.length > 0 && !palHidden

  function accept(name: string) {
    onChange(`/${name} `)
    setPalHidden(false)
    setPalIdx(0)
    inputRef.current?.focus()
  }

  function pickModel(model: string, effort: string) {
    onSelectModel?.(model, effort)
    onChange('')
    setPalHidden(false)
    inputRef.current?.focus()
  }

  return (
    <div className="shrink-0 p-3">
      <div className="relative">
        {modelOpen && <ModelPicker currentModel={modelOverride ?? null} currentEffort={effortOverride ?? null} onPick={pickModel} />}
        {palOpen && <CommandPalette items={palItems} activeIndex={palIdx} onHover={setPalIdx} onPick={accept} />}
        <div className={`rounded-lg border bg-sunken ${locked ? 'border-line opacity-60' : 'border-line focus-within:border-accent'}`}>
          <textarea
            ref={inputRef}
            className="max-h-48 min-h-[4.5rem] w-full resize-none overflow-y-auto bg-transparent px-3 pt-2.5 text-sm leading-relaxed text-fg outline-none placeholder:text-faint disabled:opacity-50"
            rows={3}
            placeholder={locked ? locked.reason : ready ? `Message ${ctxLabel} SuperMe…` : 'connecting…'}
            value={value}
            disabled={!ready || !!locked}
            onChange={(e) => {
              const next = e.target.value
              // Palette just opened (became a "/token") — pull a fresh list so it never lags a publish.
              if (/^\/\S*$/.test(next) && !/^\/\S*$/.test(value)) onPaletteOpen?.()
              onChange(next)
              setPalHidden(false)
              setPalIdx(0)
            }}
            onKeyDown={(e) => {
              if (modelOpen) {
                // Picker is click-driven; don't let Enter fire a bare "/model" message.
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); return }
                if (e.key === 'Escape') { e.preventDefault(); setPalHidden(true); return }
              }
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
              if (e.key === 'Enter' && !e.shiftKey && !locked) {
                e.preventDefault()
                onSend()
              }
            }}
          />
          <div className="flex items-center justify-between gap-2 px-2.5 pb-2 pt-1">
            <span className="truncate text-[10px] text-faint">
              {locked ? locked.reason : '/ commands · Enter to send · Shift+Enter for newline'}
            </span>
            <button
              className="flex shrink-0 items-center justify-center rounded-md bg-[var(--chat-accent)] p-1.5 text-on-accent disabled:opacity-40"
              disabled={busy || !ready || !value.trim() || !!locked}
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
