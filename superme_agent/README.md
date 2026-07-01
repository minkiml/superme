# SuperMe — a Slack-hosted Claude agent

A Slack bot whose "brain" is the **Claude Agent SDK**. It behaves like Claude Code,
but lives in Slack: you `@mention` it in a thread and it answers, reads the channel,
edits files, runs commands (with your approval), searches the web, and more.

Slack Bolt is just the I/O layer (receive mention → post reply, run the ✅/❌
approval flow). The agent loop, tools, skills, and memory are the SDK's.

## Architecture — host app vs workspace

Three layers, kept separate (full design in [docs/DEV-ROADMAP.md](docs/DEV-ROADMAP.md)):

```
APP (runtime/)      Slack I/O, query loop, sessions, approvals, channel→workspace resolver
HARNESS (harness/)  the agent's PORTABLE brain — persona + skills + subagents + tools + policy
                    loaded cwd-independently, so it's identical in every workspace
WORKSPACE           a cwd (codebase/project) + its own .claude harness — swaps per channel
```

- The **harness** loads via a local plugin (`plugins=[…]`) + persona + in-process
  tools + policy — none of which depend on `cwd`. So the agent stays "itself"
  wherever it works.
- A **workspace** is **defined** in `config/registry.yaml` (name → cwd + extras) and
  **assigned to a channel** from Slack: `@bot workspace use <name>`. A channel is
  configurable until its first conversation, then **locks** to one workspace for life
  (one channel = one workspace). Unconfigured channels use the **default = repo root**.
  `setting_sources=["project","local"]` loads that workspace's own `CLAUDE.md`/skills
  on top of the harness. Full details: [docs/channel-workspaces.md](docs/channel-workspaces.md).

  Workspace commands:
  - `@bot workspace` — show this channel's workspace (and whether it's locked)
  - `@bot workspace use <name>` — assign (only before the channel locks)
  - `@bot workspace reset` — back to default (only before the channel locks)

## Project layout
```
superme/                              # repo root = default workspace (+ future global harness)
├── superme_agent/                    # the host app (APP_DIR: .env, .sessions.json live here)
│   ├── __main__.py                   # entrypoint: python -m superme_agent
│   ├── runtime/                      # APP layer
│   │   ├── config.py                 #   env, tokens, paths (APP_DIR vs ROOT_DIR)
│   │   ├── slack_app.py              #   Bolt app + mention handler (placeholder, chunking)
│   │   ├── agent.py                  #   builds options (harness + workspace), query loop
│   │   ├── sessions.py               #   thread→session_id persistence
│   │   ├── permissions.py            #   ✅/❌ reaction approval flow
│   │   └── workspaces.py             #   channel → workspace resolver
│   ├── harness/                      # HARNESS layer (portable brain)
│   │   ├── SELF.md                   #   voice + routing rules
│   │   ├── policy.py                 #   SAFE_TOOLS / approval policy
│   │   ├── tools/slack_tools.py      #   in-process read_channel / read_thread
│   │   └── plugin/                   #   local plugin: skills/ + agents/ (cwd-independent)
│   ├── config/registry.yaml          # workspace definitions (links live in .channel_links.json)
│   └── scripts/                      # check_tokens.py, slack-app-manifest.yaml
├── docs/DEV-ROADMAP.md
├── requirements.txt
└── .gitignore
```

## Prerequisites
- Python 3.10+, Node.js (the SDK wraps the Claude Code runtime).
- A conda env (this project uses `my-agent`).
- **Auth (OAuth, Claude Max):** `claude setup-token` → put the `sk-ant-oat01-…` token
  in the single repo-root `.env` (copy `.env.example`) as `CLAUDE_CODE_OAUTH_TOKEN`. Keep
  `ANTHROPIC_API_KEY` unset (it silently takes precedence). Subscription auth is licensed
  for *individual* use.

## Slack app setup (one time)
Create the app from the manifest at [superme_agent/scripts/slack-app-manifest.yaml](superme_agent/scripts/slack-app-manifest.yaml):
1. https://api.slack.com/apps → **Create New App → From scratch** → pick your workspace.
2. **App Manifest** (left sidebar) → paste the manifest (JSON or YAML) → **Save**.
3. **Basic Information → App-Level Tokens** → generate one with `connections:write`
   → `xapp-…` → `SLACK_APP_TOKEN`.
4. **Install App → Install to Workspace** → `xoxb-…` → `SLACK_BOT_TOKEN`.
5. `/invite @your-bot` into a channel.

**Bot scopes** (in the manifest): `app_mentions:read`, `chat:write`,
`channels:history`/`channels:read` (public), `groups:history`/`groups:read` (private),
`reactions:read`/`reactions:write` (approvals). **Bot events:** `app_mention`,
`reaction_added`. Adding scopes/events later requires a **reinstall**.

Verify auth + scopes any time:
```bash
python -m superme_agent.scripts.check_tokens
```

## Run
```bash
cd superme
conda activate my-agent
pip install -r requirements.txt          # first time
python -m superme_agent                   # leave open; "⚡️ Bolt app is running!" = live
```

## Try it in Slack
- `@bot summarize the notes I dropped in this channel as action items`
  → the read-only **`read_channel`** tool (no approval).
- `@bot summarize this channel including what's inside the threads`
  → the **channel-deep-scan** skill (`read_channel` + `read_thread`).
- `@bot summarize that thread <paste a Slack thread link>`
  → `read_thread` accepts a pasted permalink (or a ts) — works across threads in the channel.
- `@bot draft release notes for v2.3: added SSO (PLAT-12), fixed retry (PLAT-31)`
  → the **release-notes** skill.
- `@bot use the code-reviewer subagent on superme_agent/runtime/agent.py`
  → the **code-reviewer** subagent.
- `@bot add a docstring to run_agent` → needs `Edit`, so you get an approval card:
  tap **✅** to allow / **❌** to deny.
- `@bot search the web for …` → `WebSearch` (no approval).

## How it behaves
- **Continuous per-thread memory:** each Slack thread is one resumable session
  (`sessions.py` + `.sessions.json`). New top-level mention = fresh conversation.
- **Processing indicator:** the bot posts `⏳ Thinking…` immediately, then replaces
  it with the answer.
- **Approvals:** read-only tools (`Read`, `Grep`, `WebSearch`, `read_channel`, …)
  auto-run; everything with side effects (`Write`, `Edit`, `Bash`, …) needs your ✅
  on the approval card. Only the requester's reaction counts.

## Notes
- Anyone who can `@mention` the bot can ask it to act (subject to approval) — treat
  channel access accordingly.
- Pin the SDK version; reference: https://docs.claude.com/en/docs/agent-sdk/python
