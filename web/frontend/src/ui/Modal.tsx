import { useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'

// The one modal shell — a full-viewport scrim + a centered content card. Every drill-in / editor /
// inspector renders through this so overlay chrome (scrim, blur, radius, border, shadow) lives in
// one place. `title` opts into the standard header row (title + close X); omit it for a fully
// custom body. Click-outside and the X both call onClose; inner clicks don't bubble to the scrim.
// `contain` swaps the viewport-filling `fixed` for `absolute` so the scrim fills the nearest
// positioned ancestor instead — used by the dev column popups that must leave the chat rail live.
export default function Modal({
  onClose,
  title,
  children,
  maxW = 'max-w-xl',
  z = 'z-50',
  scrim = 'bg-black/50',
  contain = false,
  column = false,
  fill = false,
  dismissable = true,
}: {
  onClose: () => void
  title?: ReactNode
  children: ReactNode
  maxW?: string // Tailwind max-width class for the content card
  z?: string // Tailwind z-index class (stack nested modals higher)
  scrim?: string // Tailwind bg class for the backdrop
  contain?: boolean // absolute (fill positioned ancestor) instead of fixed (viewport)
  dismissable?: boolean // false ⇒ a backdrop click never closes; only the X / an explicit action
  // does. Pass `!editing` for editors, so an outside click can't silently discard unsaved input.
  column?: boolean // cap the card to the viewport/column and lay it out as a flex column, so a
  // caller with a pinned header/footer + a `flex-1 min-h-0 overflow-y-auto` body scrolls internally
  fill?: boolean // with `column`: TAKE the full height rather than just capping it, so the card is
  // a stable frame instead of resizing to each tab's content (adaptive — it's the scrim's height,
  // which the viewport/column drives). Use for multi-tab inspectors; omit for short prompts.
}) {
  // Close on a true backdrop click only. A `click` resolves to the common ancestor of its mousedown
  // and mouseup, so a press that STARTS inside the card and releases on the scrim (e.g. dragging a
  // text selection out) would otherwise resolve to the scrim and close it. Guard on both ends: the
  // press must start on the scrim (downOnScrim) AND the click must target the scrim itself.
  const downOnScrim = useRef(false)
  return (
    <div
      className={`${contain ? 'absolute' : 'fixed'} inset-0 ${z} grid place-items-center ${scrim} p-3 backdrop-blur-sm sm:p-6`}
      onMouseDown={(e) => { downOnScrim.current = e.target === e.currentTarget }}
      onMouseUp={(e) => {
        if (dismissable && downOnScrim.current && e.target === e.currentTarget) onClose()
        downOnScrim.current = false
      }}
    >
      <div
        className={`w-full ${maxW} ${column ? `flex flex-col ${fill ? 'h-full' : 'max-h-full'}` : ''} overflow-hidden rounded-2xl border border-line bg-app shadow-2xl`}
      >
        {title !== undefined && (
          <div className="flex items-center justify-between border-b border-line px-5 py-4">
            <span className="text-[15px] font-semibold text-fg">{title}</span>
            <button onClick={onClose} className="rounded-md p-1 text-muted hover:bg-hover hover:text-fg">
              <X size={18} />
            </button>
          </div>
        )}
        {children}
      </div>
    </div>
  )
}
