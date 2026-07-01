import { User } from 'lucide-react'
import PageHeader from '@/ui/PageHeader'

// "Me" — the overview scene for global SuperMe. Sub-categories are navigated from the
// sidebar; this is just the overview.
export default function MePage() {
  return (
    <div className="flex h-full flex-col">
      <PageHeader icon={User} title="Me" subtitle="Your global SuperMe · knowledge is global" />
      <div className="flex-1 overflow-auto p-6">
        <div className="max-w-2xl rounded-xl border border-line bg-surface p-5">
          <p className="text-sm leading-relaxed text-muted">
            Open a sub-category from the sidebar to focus on one slice of what SuperMe knows.
            These are early placeholders while we design how to present it.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <span className="rounded-md bg-hover px-2.5 py-1 text-xs text-muted">
              Chat → the panel on the right
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
