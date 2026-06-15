import { Boxes, Database } from 'lucide-react'
import PageHeader from '@/ui/PageHeader'
import type { ContextRef } from '@/lib/contexts'

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-3 px-4 py-2.5">
      <dt className="w-28 shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 break-all font-mono text-xs text-fg">{value}</dd>
    </div>
  )
}

// The presentation scene for one connected domain (a project-level sub-SuperMe).
export default function DomainScene({
  domain,
  onManageKnowledge,
}: {
  domain: ContextRef
  onManageKnowledge: () => void
}) {
  return (
    <div className="flex h-full flex-col">
      <PageHeader
        icon={Boxes}
        title={domain.label}
        subtitle="A connected project · its own sub-SuperMe"
        badge="domain"
        right={
          <button
            onClick={onManageKnowledge}
            title="Open this domain’s knowledge"
            className="inline-flex items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-muted hover:bg-hover hover:text-fg"
          >
            <Database size={15} /> Knowledge
          </button>
        }
      />
      <div className="flex-1 overflow-auto p-6">
        <dl className="max-w-2xl divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface text-sm">
          <Field label="Context id" value={domain.id} />
          {domain.cwd && <Field label="Working dir" value={domain.cwd} />}
        </dl>
        <p className="mt-4 text-xs text-muted">
          Pick “{domain.label}” in the chat context selector to talk to it.
        </p>
      </div>
    </div>
  )
}
