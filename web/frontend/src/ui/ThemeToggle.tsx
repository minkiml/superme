import { useState } from 'react'
import { getTheme, setTheme, type Theme } from '@/lib/theme'

// Two-state light/dark toggle for the sidebar footer.
export default function ThemeToggle() {
  const [t, setT] = useState<Theme>(getTheme())
  function pick(next: Theme) {
    setTheme(next)
    setT(next)
  }
  return (
    <div className="flex items-center gap-0.5 rounded-md bg-hover p-0.5">
      {(['light', 'dark'] as Theme[]).map((opt) => (
        <button
          key={opt}
          onClick={() => pick(opt)}
          className={`flex-1 rounded px-2 py-1 text-xs capitalize ${
            t === opt ? 'bg-surface text-fg' : 'text-muted hover:text-fg'
          }`}
        >
          {opt === 'light' ? '☀ light' : '☾ dark'}
        </button>
      ))}
    </div>
  )
}
