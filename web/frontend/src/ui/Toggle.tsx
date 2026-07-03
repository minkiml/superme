// The boolean switch — a sliding knob. Defaults to the `core` accent when on (the learning /
// settings surfaces); pass `onColor` to tint it per context.
export default function Toggle({
  on,
  onChange,
  onColor = 'bg-core',
  disabled = false,
  title,
}: {
  on: boolean
  onChange: (v: boolean) => void
  onColor?: string
  disabled?: boolean
  title?: string
}) {
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-50 ${on ? onColor : 'bg-hover'}`}
    >
      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-app transition-all ${on ? 'left-[18px]' : 'left-0.5'}`} />
    </button>
  )
}
