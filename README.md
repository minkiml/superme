# SuperMe

A local dashboard for running Claude Code agents against your own repositories.

Everything it stores is local to your machine: the SQLite stores, your knowledge home, and
your repo registry are all gitignored and never leave it. Agent turns run through the Claude
Code CLI, so they reach Anthropic exactly the way `claude` does and no further.

## Requirements

- **Python 3.11+** and a virtual environment of your choosing
- **Node.js** with npm
- The **[Claude Code](https://claude.com/claude-code) CLI**, signed in to a Claude plan

## Setup

**1 · Install dependencies** into your environment.

```bash
pip install -r requirements.txt
npm install --prefix web/frontend
```

**2 · Configure SuperMe.** This writes the local config a checkout does not carry — your `.env`,
your repo registry, your knowledge home, and the two SQLite stores. It installs nothing, and
re-running it is safe.

```bash
python setup_superme.py
```

**3 · Have a credential.** Either one works, and you may already have the first:

- **You are signed in to the Claude CLI** — `claude auth login`, or you signed in when you
  installed it. Nothing else to do; SuperMe uses the same credential `claude` does.
- **Or put a long-lived token in `.env`** — run `claude setup-token` and add it:

  ```
  CLAUDE_CODE_OAUTH_TOKEN=...
  ```

SuperMe runs on Claude plan auth only. `ANTHROPIC_API_KEY` is dropped from the process
whether or not you set it, so a key in your shell can never quietly bill you instead.

With neither, SuperMe still runs and the dashboard still opens — it says so in a banner across
the top and greys out everything that would need an agent, so nothing starts a run that cannot
finish. Sign in, then click **I signed in** in that banner.

Re-run `python setup_superme.py --check` at any point to see what is still missing. It reports
without writing anything.

## Run

```bash
python run_superme.py
```

That starts the core daemon (`:8787`), the web BFF (`:8000`) and the frontend (`:5173`), then
open **http://localhost:5173**. Ctrl-C stops all three. Ports come from `.env`.
