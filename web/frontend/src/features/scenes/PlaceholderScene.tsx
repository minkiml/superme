import { Sparkles } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import PageHeader from '@/ui/PageHeader'

// A sub-category scene that isn't designed yet (each Me sub-category loads one).
export default function PlaceholderScene({
  title,
  blurb,
  icon,
}: {
  title: string
  blurb: string
  icon?: LucideIcon
}) {
  return (
    <div className="flex h-full flex-col">
      <PageHeader icon={icon ?? Sparkles} title={title} subtitle={blurb} badge="placeholder" />
      <div className="flex-1 overflow-auto p-6">
        <div className="max-w-xl rounded-xl border border-dashed border-line bg-surface p-6 text-sm text-muted">
          This scene is a placeholder. How we present and interact with this slice of knowledge
          is still being designed.
        </div>
      </div>
    </div>
  )
}
