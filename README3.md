# SuperMe

**SuperMe** is a local-only, dashboard-driven personal agent system. One AI identity hosted onto
every repository you own, engineered around a single owner rather than a team. Its goal is that
everything you build and run with AI stops being scattered sessions and starts accumulating into one
knowledge estate that represents you, and that you stay in command of it without becoming its
bottleneck.

![The Nexus, every connected repo, live](docs/img/nexus.png)

---

### Your AI use builds a digital twin of you

Your work isn't one repo. It isn't one project, one workspace, one session, one AI activity. It's an
**estate of knowledge that represents you**. Projects, side work, a business, the tooling that runs
all of it, and every decision you made along the way.

Today that estate is shattered. A thread per session, a context per tool, a memory per project. None
of them know about each other, none of them survive the session, and none of them can be connected
into anything central. You are the only integration layer, and you are doing it from memory.

SuperMe is that layer. One identity, hosted onto every repo you own including its own codebase, where
every repo becomes knowledge the system reasons over, and every body of knowledge can become a repo.
Building and operating happen in the same place, under an identity that accumulates instead of
resetting. What it learns from working becomes skills and rules, one approval at a time.

*(illustration, knowledge and memory structure. Universal harness vs per-host knowledge, hub vs
project hosts, core vs dev)*

---

### Human-in-the-loop is the principle. Human throughput is the engineering.

That the human decides is settled here. Output nobody verified isn't performance, it's exposure.
Every phase gates on the owner, and autopilot never crosses review.

Which puts you on the critical path of everything. AI keeps getting better and faster. You don't.
Agents now produce more in an hour than one person can read in a day, so **every gate is a stop, and
the sum of those stops is the system's real throughput.** The bottleneck isn't the agent's speed.
It's your comprehension.

So the hard problem in SuperMe isn't *whether* to gate. It's what a gate costs you. The system is
continuously rebuilt around one question.

> At this gate, in this situation, what gets the owner to a confident decision in the least time?

Which is really a stack of harder ones, and these are live design work rather than settled features.

- **What actually needs your judgment here**, and what should have been checked mechanically before
  you were ever asked?
- **What must be exposed at this gate, and what is noise?** The answer differs by gate, by kind of
  work, and by how much you already know.
- **How should a code change be presented** so it's understood rather than skimmed?
- **Which artifacts earn their existence**, and what skills write them well enough to be worth your
  time?
- **How do you come back cold?** You step away for two days and return with nearly fresh memory of
  work that was mid-flight. Fast, faithful context restoration is a first-class problem here, not an
  afterthought.

*(illustration, comprehension flow. item → artifacts → mechanical checks → report → your decision
→ trace)*

What that discipline has produced so far.

**Reports written for you, not logs.** What it did, what to push back on, how much to trust each
claim, and where it leaves the project.

![A gate report](docs/img/report.png)

**Mechanical checks first.** Missing artifacts, stale evidence and unresolved authorizations are
settled before you're asked, so your attention goes to judgment instead of bookkeeping.

**Self-carrying work-items.** Brief, plan, decisions, checkpoints and history travel with the item,
so returning to it is reading, not reconstructing.

![A work-item](docs/img/work-item.png)

**Interactive review.** Read the diff and leave feedback in the item. Feedback becomes a check, build
implements it, vet re-runs.

![PR preview and review](docs/img/review.png)

**Full observability.** Every run keeps its trace. Prompts, tool calls, results, sub-agents, tokens,
context fill. Nothing is a black box when it goes wrong.

![Execution trace](docs/img/trace.png)

---

### Other features

- **Git worktrees.** Every item builds in its own worktree, so parallel agent work never collides
  and nothing lands until you approve it.
- **Auto task-breaking.** A brief too large for one pass fans out into children, parallel where they
  can be, sequential where they must be.
- **OS-level sandbox.** Agent shell commands are held inside their working root by the kernel, not
  by a prompt.
- **Clear memory boundaries.** An explicit answer to *when* something is written (a work-item phase)
  and *what kind* it is. Operational content that governs behaviour is versioned with the code.
  Knowledge lives outside it and is pulled on demand. The two never mix.
- **Kernel-owned context management.** Compaction fires on a run boundary at a threshold you set,
  never mid-task, always checkpointed first.
- **Plan auth only.** `ANTHROPIC_API_KEY` is dropped from the process whether or not you set it, so
  a key in your shell can never quietly bill you.

### Coming

- **Multi-LLM engine support.** Claude and OpenAI, same harness, same gates.
- **Operational knowledge and memory structure.** The core-mode twin, the taxonomy and retrieval
  model for the non-code half of the estate.

### Out of scope

- **Team platforms.** No seats, no roles, no sharing model. One owner, though the projects they
  connect can be a team's, or a business's.
- **Hosting.** No cloud, no account, no telemetry. It runs on your machine and stays there.
- **Model-agnosticism.** One harness, one auth path. No API keys, no model zoo.
- **Chat as the product.** Chat exists, but the work doesn't run there.

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

**2 · Configure SuperMe.** This writes the local config a checkout does not carry. Your `.env`,
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
