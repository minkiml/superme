# SuperMe — capabilities

What SuperMe can do right now. Present tense only: an entry lands when the deliverable carrying it closes, never when it's planned.

## Capabilities
- **Gate reports** — a report written for the owner at every gate: what happened, what to push back on, how much to trust each claim, and where it leaves the project (arrives automatically at each gate).
- **Mechanical checks before you're asked** — missing artifacts, stale evidence and unresolved authorizations are settled before a gate reaches the owner.
- **Self-carrying work-items** — a work-item's brief, plan, decisions, checkpoints and history travel with it, so returning to it is reading, not reconstructing.
- **Interactive review** — read a diff and leave feedback directly in the item; the feedback becomes a check, the next build implements it, vet re-runs it.
- **Full execution observability** — every run keeps its trace: prompts, tool calls, results, sub-agents, tokens, context fill.
- **Isolated per-item git worktrees** — every work-item builds in its own worktree, so parallel agent work never collides and nothing lands until the owner approves it.
- **Auto task-breaking** — a brief too large for one pass fans out into child work-items, parallel where they can run and sequential where they must.
- **OS-level shell sandbox (macOS)** — agent shell commands are held inside their working root by the kernel, not by prompt instruction.
- **Separated memory boundaries** — operational content that governs agent behaviour is versioned with the code; knowledge about the world is pulled on demand from the knowledge store; the two are never mixed.
- **Kernel-owned context management** — compaction fires only on a run boundary at an owner-set threshold, always checkpointed first, never mid-task.
