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

**3 · Add your credential.** Run `claude setup-token`, then put the token in `.env`:

```
CLAUDE_CODE_OAUTH_TOKEN=...
```

SuperMe runs on Claude plan auth only. `ANTHROPIC_API_KEY` is dropped from the process
whether or not you set it, so a key in your shell can never quietly bill you instead.

Re-run `python setup_superme.py --check` at any point to see what is still missing. It reports
without writing anything.

## Run

```bash
python run_superme.py
```

That starts the core daemon (`:8787`), the web BFF (`:8000`) and the frontend (`:5173`), then
open **http://localhost:5173**. Ctrl-C stops all three. Ports come from `.env`.
