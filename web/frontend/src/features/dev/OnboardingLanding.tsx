import { Sparkles } from 'lucide-react'

// The onboarding front door (S5 · slice B). A repo whose project memory isn't established yet (its
// PRD defines no deliverables) shows THIS instead of the work tabs — a hard gate: the first thing
// you can do in a new repo's dev workspace is establish its memory, not take on work.
//
// There is deliberately NO start button. Onboarding is a repo STATE, not an action: while the repo
// is unestablished, every session in it already IS an onboarding session (ws.py `is_onboarding`),
// and `onboarding_preamble(mode)` already carries the skill directive in the system prompt. So a
// "Start" button that typed a canned "Run **retrofit**: …" into the chat only said out loud what
// the system was already saying privately — noise in the owner's transcript, and a worse opening
// than the one thing we actually want from them: what this project IS. This page's job is to say
// that, and get out of the way. The repo leaves this state on its own when the anchor docs land.

export type OnboardMode = 'project-init' | 'retrofit'

// What SuperMe will DO, per connect-time mode — in the owner's words, not ours. Deliberately does
// NOT name the skill (`retrofit`/`project-init`): those are our internal identifiers, and a user
// reading "I'll retrofit" learns nothing the phrase "I'll read the code" doesn't say plainly.
const WILL_DO: Record<OnboardMode, string> = {
  'project-init': "I'll draft the anchor docs with you from there",
  retrofit: "I'll read the code and draft the anchor docs with you",
}
// No stored choice (a repo that predates the connect flow) — promise only what's true either way.
const WILL_DO_DEFAULT = "I'll draft the anchor docs with you"

// The connect-time choice, stated and nothing more — the owner made it and can change it by saying
// so; spelling out what it implies is explaining our own machinery back at them.
const PATHS: Record<OnboardMode, string> = {
  'project-init': 'a new project',
  retrofit: 'an existing codebase',
}

export default function OnboardingLanding({
  repoLabel,
  mode = null,
}: {
  repoLabel: string
  mode?: OnboardMode | null // the connect-time choice; drives the copy (and, silently, the skill)
}) {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-xl px-6 py-14">
        <div className="mb-1.5 flex items-center gap-1.5 text-dev">
          <Sparkles size={14} />
          <span className="text-[11px] font-medium uppercase tracking-wider">Onboarding</span>
        </div>
        <h1 className="text-[20px] font-semibold text-fg">Set up {repoLabel}</h1>
        <p className="mt-2.5 text-[13.5px] leading-relaxed text-muted">
          SuperMe has no memory of this project yet. <span className="text-fg">Tell me briefly in the
          chat what you're building</span>, and {mode ? WILL_DO[mode] : WILL_DO_DEFAULT}. The work
          tabs unlock once they land.
        </p>
        {mode && (
          <p className="mt-3 text-[12.5px] text-faint">Connected as {PATHS[mode]}.</p>
        )}
      </div>
    </div>
  )
}
