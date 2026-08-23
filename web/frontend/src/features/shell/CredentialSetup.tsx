import { useState } from 'react'
import { KeyRound, Terminal, Check, Copy, ArrowRight, Download, FileText } from 'lucide-react'
import { getAuthStatus, type AuthStatus } from '@/lib/api'
import { invalidate } from '@/lib/live'
import { K } from '@/lib/live/keys'

// The first screen of an install that cannot reach Anthropic yet.
//
// This replaces the dashboard rather than sitting on top of it: with no credential every agent
// action is refused, so a working-looking cockpit is a lie a new owner has to discover by
// clicking. The page's job is to be finishable — the two ways in, the exact commands, and a way
// to say "done" without restarting anything.

function Command({ text, icon: Icon = Terminal, wrap = false }: {
  text: string
  icon?: typeof Terminal
  wrap?: boolean          // a file path is worth two lines; truncating one helps nobody
}) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="mt-2 flex items-start gap-2 rounded-md border border-line bg-sunken px-3 py-2">
      <Icon size={13} className="mt-0.5 shrink-0 text-faint" />
      <code className={`min-w-0 flex-1 font-mono text-[12.5px] text-fg ${wrap ? 'break-all' : 'truncate'}`}>{text}</code>
      <button
        onClick={() => {
          navigator.clipboard?.writeText(text).then(() => {
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
          }).catch(() => {})
        }}
        title="Copy"
        aria-label={`Copy: ${text}`}
        className="-mt-0.5 shrink-0 rounded p-1 text-faint transition-colors hover:text-accent-text"
      >
        {copied ? <Check size={13} className="text-success" /> : <Copy size={13} />}
      </button>
    </div>
  )
}

// No step numbers: installing the CLI is a prerequisite and the two credentials are alternatives,
// so a running count would promise an order the page does not have.
function Step({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <h2 className="text-[14px] font-medium text-fg">{title}</h2>
      <div className="mt-1.5 text-[13px] leading-relaxed text-muted">{children}</div>
    </div>
  )
}

export default function CredentialSetup({ status, onSkip }: {
  status: AuthStatus | undefined
  onSkip: () => void
}) {
  const [checking, setChecking] = useState(false)
  const [stillMissing, setStillMissing] = useState(false)

  function recheck() {
    setChecking(true)
    setStillMissing(false)
    getAuthStatus(true)
      .then((s) => { if (!s.ready) setStillMissing(true) })
      .then(() => invalidate(K.authStatus))
      .catch(() => {})
      .finally(() => setChecking(false))
  }

  return (
    <div className="h-full overflow-y-auto bg-app">
      <div className="mx-auto max-w-2xl px-6 py-16">
        <div className="mb-1.5 flex items-center gap-1.5 text-accent-text">
          <KeyRound size={14} />
          <span className="text-[11px] font-medium uppercase tracking-wider">Setup</span>
        </div>
        <h1 className="text-[22px] font-semibold text-fg">Connect Claude</h1>
        <p className="mt-2.5 text-[13.5px] leading-relaxed text-muted">
          SuperMe runs every agent turn through Claude Code on your Claude plan. Set up either one
          below.
        </p>

        <div className="mt-7 flex flex-col gap-3">
          {status && !status.cli_installed && (
            <Step title="Install Claude Code first">
              SuperMe needs it for every turn.
              <div className="mt-2">
                <a
                  href="https://claude.com/claude-code"
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex items-center gap-1.5 text-[12.5px] text-accent-text hover:underline"
                >
                  <Download size={13} /> claude.com/claude-code
                </a>
              </div>
            </Step>
          )}

          <Step title="Add a token to .env">
            Print a token.
            <Command text="claude setup-token" />
            <div className="mt-3">Add it to this file on its own line.</div>
            {status?.env_file && <Command text={status.env_file} icon={FileText} wrap />}
            <Command text="CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-…" icon={KeyRound} />
          </Step>

          <div className="flex items-center gap-3 px-1">
            <div className="h-px flex-1 bg-line" />
            <span className="text-[11px] uppercase tracking-wider text-faint">or</span>
            <div className="h-px flex-1 bg-line" />
          </div>

          <Step title="Sign in to the CLI">
            Nothing to paste. SuperMe uses the same credential as
            <code className="font-mono text-[12px] text-fg"> claude </code>.
            <Command text="claude auth login" />
          </Step>
        </div>

        <div className="mt-7 flex flex-wrap items-center gap-3">
          <button
            onClick={recheck}
            disabled={checking}
            className="rounded-md bg-accent px-4 py-2 text-[13px] font-medium text-on-accent transition hover:opacity-90 disabled:opacity-50"
          >
            {checking ? 'Checking…' : "I've done that"}
          </button>
          <button
            onClick={onSkip}
            className="inline-flex items-center gap-1.5 text-[12.5px] text-muted transition-colors hover:text-fg"
          >
            Look around first <ArrowRight size={13} />
          </button>
        </div>

        {stillMissing && status && (
          <p className="mt-3 text-[12.5px] leading-relaxed text-warn">
            {status.detail}
          </p>
        )}

      </div>
    </div>
  )
}
