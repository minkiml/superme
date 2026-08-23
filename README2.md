# SuperMe

SuperMe is a personal agent system that runs on your own machine, with a local dashboard.
It is built for one person rather than a team. The goal is that everything you build and run with AI collects into a single body of knowledge that represents you, and that you stay in charge of it without becoming the thing that slows it down.

---

### Your AI use builds a digital twin of you

Your work is not one repo. It is not one project, one workspace, one session, one AI conversation.
It is a body of knowledge that represents you. Your projects, your side work, your business, and
the tooling that runs all of it.

Right now that knowledge is scattered. One thread per session. One set of knowledge and memory per
project. None of them know about each other, and none of them are connected. You are the only
thing connecting them, and you are doing it from memory.

SuperMe is that connection. One identity works across every repo you own, including its own code.
Every repo becomes knowledge the system can read. Every body of knowledge can become a repo. You
build and you operate in the same place, with an identity that keeps what it learns instead of
starting over. What it learns from the work turns into skills and rules, and you approve each one.

*(illustration, the structure of SuperMe knowledge and memory)*

---

### Human in the loop is the principle. Human throughput is the work.

You decide. That part is settled. Work nobody checked is not speed, it is risk. So every phase stops
at a gate you hold, and autopilot never crosses review.

That puts you in front of everything. AI keeps getting faster and you do not. An agent can write
more in an hour than you can read in a day. Every gate is a stop, and all those stops added together
are the real speed of the system. The slow part is not the agent. It is how fast you can understand
what it did.

So the hard problem here is not whether to stop at a gate. It is what stopping costs you. One
question keeps driving the design.

> At this gate, right now, what gets you to a confident decision in the least time?

That question breaks into harder ones, and they are all still open.

- What here actually needs your judgment, and what should the system have checked before it asked you?
- What has to be shown at this gate, and what is just noise? The answer changes with the gate, with
  the kind of work, and with how much you already know.
- How do you present a code change so that someone reads it properly instead of skimming it?
- Which documents are worth writing at all, and what makes one good enough to be worth your time?
- How do you pick work back up after being away? You leave for two days and come back with almost no
  memory of what was in flight. Getting you back up to speed quickly is a real problem, not an
  afterthought.

*(illustration, how a work item reaches you at a gate)*

Here is what has come out of that so far.

Reports are written for you rather than kept as a log. Each one says what was done, what you should
push back on, how much to trust each claim, and where the project now stands.

![A gate report](docs/img/report.png)

Checks run before you are asked. Missing documents, stale evidence and open approvals are settled by
the system first, so your attention goes to the decision instead of the bookkeeping.

Work items carry themselves. The brief, the plan, the decisions and the history all live with the
item, so coming back to one means reading rather than rebuilding.

![A work item](docs/img/work-item.png)

Review is where you steer. Read the diff and leave feedback on the item. Your feedback becomes a
check, the agent implements it, and verification runs again.

![PR preview and review](docs/img/review.png)

Every run keeps its full trace. Prompts, tool calls, results, sub agents, tokens and context use.
When something goes wrong you can look at what happened instead of guessing.

![Execution trace](docs/img/trace.png)

---

### Other features

- Each work item builds in its own git worktree, so agents working in parallel never collide and
  nothing lands until you approve it.
- A brief too large for one pass splits into smaller items. They run in parallel where they can and
  in order where they have to.
- Shell commands run inside an OS sandbox, held to their working folder by the operating system
  rather than by an instruction.
- Memory has clear boundaries. Content that governs how the agent behaves is versioned with the
  code. Knowledge about your work lives outside it and is loaded only when needed. The two never mix.
- Context is managed by the system. Compaction runs between runs, at a level you set, never in the
  middle of a task, and always after a checkpoint is saved.
- SuperMe runs on plan auth only. `ANTHROPIC_API_KEY` is dropped from the process whether or not you
  set it, so a key sitting in your shell can never quietly bill you.

### Coming

- Support for more than one LLM engine. Claude and OpenAI, on the same harness, through the same
  gates.
- A real structure for operational knowledge and memory, which is the taxonomy and retrieval model
  for the half of your work that is not code.

### Out of scope

- Team platforms. No seats, no roles, no sharing. One owner, though the projects you connect can
  belong to a team or a business.
- Hosting. No cloud, no account, no telemetry. It runs on your machine and stays there.
- Model choice. One harness and one auth path. No API keys and no model zoo.
- Chat as the product. Chat is there, but the work does not happen in it.

---

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

**2 · Configure SuperMe.** This writes the local config a checkout does not carry. Your `.env`, your
repo registry, your knowledge home, and the two SQLite stores. It installs nothing, and re-running
it is safe.

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
