import { Sparkles, FolderGit2, ArrowRight } from 'lucide-react'

// The onboarding front door (S5 · slice B). A repo whose project memory isn't established yet
// (its PRD defines no deliverables) shows THIS instead of the work tabs — a hard gate: the first
// thing you can do in a new repo's dev workspace is establish its memory, not take on work. The two
// paths mirror the onboarding skills: project-init (new/greenfield) and retrofit (existing code).
// Picking one seeds a guided onboarding session into the chat rail.

export type OnboardMode = 'project-init' | 'retrofit'

const PATHS: Record<OnboardMode, { title: string; blurb: string }> = {
  'project-init': {
    title: 'New project',
    blurb: 'Starting fresh. SuperMe grills you through the vision, scope, and deliverables, then drafts the anchor docs forward — PRD and roadmap first.',
  },
  retrofit: {
    title: 'Existing codebase',
    blurb: 'Already has real code. SuperMe reads the code to reconstruct the architecture, clarifies the intent with you, and infers the rest.',
  },
}

export default function OnboardingLanding({
  repoLabel,
  mode = null,
  onStart,
}: {
  repoLabel: string
  mode?: OnboardMode | null // the connect-time choice; when set, launch it directly (null = let the owner pick)
  onStart: (mode: OnboardMode) => void
}) {
  const other: OnboardMode = mode === 'project-init' ? 'retrofit' : 'project-init'
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl px-6 py-12">
        <div className="mb-2 flex items-center gap-2 text-dev">
          <Sparkles size={18} />
          <span className="text-[12px] font-medium uppercase tracking-wider">Establish project memory</span>
        </div>
        <h1 className="text-[22px] font-semibold text-fg">Set up {repoLabel}</h1>
        <p className="mt-2 text-[13.5px] leading-relaxed text-muted">
          SuperMe has no durable memory of this project yet. Before it can take on work here, establish
          its <span className="text-fg">anchor docs</span> — the living record of what this project is,
          how it's built, and what's in motion. SuperMe drafts the docs with you and commits them once
          you approve.
        </p>

        {mode ? (
          // The connect-time choice picked the path — launch it directly, with an escape to the other.
          <div className="mt-8">
            <ChoiceCard
              icon={mode === 'project-init' ? <Sparkles size={18} /> : <FolderGit2 size={18} />}
              title={PATHS[mode].title}
              skill={mode}
              blurb={PATHS[mode].blurb}
              onStart={() => onStart(mode)}
              wide
            />
            <button
              onClick={() => onStart(other)}
              className="mt-3 text-[12px] text-faint underline-offset-2 hover:text-muted hover:underline"
            >
              Not a {mode === 'project-init' ? 'new' : 'existing'} project? Use {other} instead.
            </button>
          </div>
        ) : (
          // No stored choice (repo predates connect-flow) — let the owner pick.
          <div className="mt-8 grid gap-4 sm:grid-cols-2">
            <ChoiceCard icon={<Sparkles size={18} />} title={PATHS['project-init'].title} skill="project-init" blurb={PATHS['project-init'].blurb} onStart={() => onStart('project-init')} />
            <ChoiceCard icon={<FolderGit2 size={18} />} title={PATHS.retrofit.title} skill="retrofit" blurb={PATHS.retrofit.blurb} onStart={() => onStart('retrofit')} />
          </div>
        )}
      </div>
    </div>
  )
}

function ChoiceCard({
  icon,
  title,
  skill,
  blurb,
  onStart,
  wide = false,
}: {
  icon: React.ReactNode
  title: string
  skill: string
  blurb: string
  onStart: () => void
  wide?: boolean
}) {
  return (
    <div className="flex flex-col rounded-xl border border-line bg-surface p-4">
      <div className="flex items-center gap-2 text-dev">{icon}</div>
      <div className="mt-2 text-[15px] font-semibold text-fg">{title}</div>
      <div className="mt-0.5 font-mono text-[11px] text-faint">{skill}</div>
      <p className={`mt-2 flex-1 text-[12.5px] leading-relaxed text-muted ${wide ? 'max-w-md' : ''}`}>{blurb}</p>
      <button
        onClick={onStart}
        className={`mt-4 inline-flex items-center justify-center gap-1.5 rounded-lg bg-dev px-3 py-2 text-[13px] font-medium text-white hover:opacity-90 ${wide ? 'self-start px-5' : ''}`}
      >
        Start <ArrowRight size={14} />
      </button>
    </div>
  )
}
