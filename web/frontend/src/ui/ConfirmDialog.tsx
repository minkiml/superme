import type { ReactNode } from 'react'

// A small modal confirm. Backdrop click cancels; the confirm button is danger-tinted by
// default (the only use so far is "forget this session"). Render conditionally by the
// caller — there's no internal open state.
export default function ConfirmDialog({
  title,
  body,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  onConfirm,
  onCancel,
}: {
  title: string
  body: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <div
      className="absolute inset-0 z-30 flex items-center justify-center bg-black/50 p-4"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-xs rounded-lg border border-line bg-surface p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-sm font-semibold text-fg">{title}</div>
        <div className="mt-1 text-xs text-muted">{body}</div>
        <div className="mt-3 flex justify-end gap-2">
          <button className="rounded bg-hover px-3 py-1.5 text-xs text-fg hover:bg-hover" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            className="rounded bg-danger px-3 py-1.5 text-xs text-on-accent"
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
