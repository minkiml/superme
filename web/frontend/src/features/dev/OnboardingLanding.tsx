import { Sparkles, KeyRound } from 'lucide-react'
import { useAuthGate } from '@/lib/authGate'

// A repo with no established memory shows THIS instead of the work tabs — a hard gate.
//
// There is deliberately NO start button: onboarding is a repo STATE, and every session in an
// unestablished repo already IS one.

export type OnboardMode = 'project-init' | 'retrofit'

// In the owner's words, not ours: naming the skill teaches nothing the plain phrase does not say.
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
  // This is the first screen of a fresh install, and its whole instruction is "use the chat".
  // With no credential the chat is greyed, so saying it anyway sends a new owner into a wall.
  const { reason: authReason } = useAuthGate()
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-xl px-6 py-14">
        <div className="mb-1.5 flex items-center gap-1.5 text-dev">
          <Sparkles size={14} />
          <span className="text-[11px] font-medium uppercase tracking-wider">Onboarding</span>
        </div>
        <h1 className="text-[20px] font-semibold text-fg">Set up {repoLabel}</h1>
        {authReason ? (
          <>
            <p className="mt-2.5 text-[13.5px] leading-relaxed text-muted">
              SuperMe has no memory of this project yet, and onboarding is a conversation — so it
              needs a credential before it can start.
            </p>
            <div className="mt-4 flex items-start gap-2.5 rounded-lg border border-warn/40 bg-warn/10 px-3.5 py-3">
              <KeyRound size={15} className="mt-0.5 shrink-0 text-warn" />
              <div className="text-[13px] leading-relaxed text-fg">
                <div className="font-medium">First, sign in</div>
                <div className="mt-1 text-muted">{authReason}</div>
                <div className="mt-2 text-muted">
                  Then come back and <span className="text-fg">tell me briefly in the chat what
                  you're building</span> — {mode ? WILL_DO[mode] : WILL_DO_DEFAULT}.
                </div>
              </div>
            </div>
          </>
        ) : (
          <p className="mt-2.5 text-[13.5px] leading-relaxed text-muted">
            SuperMe has no memory of this project yet. <span className="text-fg">Tell me briefly in the
            chat what you're building</span>, and {mode ? WILL_DO[mode] : WILL_DO_DEFAULT}. The work
            tabs unlock once they land.
          </p>
        )}
        {mode && (
          <p className="mt-3 text-[12.5px] text-faint">Connected as {PATHS[mode]}.</p>
        )}
      </div>
    </div>
  )
}
