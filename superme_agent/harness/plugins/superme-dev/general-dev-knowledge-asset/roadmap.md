# Authoring guide — `general/roadmap.md`

**Purpose.** The per-repo **dev index** — a forward map of the work being driven *through SuperMe*,
organized by the deliverable it serves. A cache of what's *in motion*, not an archive of what's built.
**Write / update.** Draft the forward scaffold at onboarding. Add a wave when new pipelined work starts;
flip a wave's glyph as it progresses.
**Length.** A quick map. Waves only — never list individual work-items (they attach themselves).

## The two-tier scaffold
**Deliverable** (defined in project-prd.md) → **Wave** (defined here) → **Work-item** (an instance that
points *up* at a wave via its own `wave:` frontmatter — never listed here). The board joins them live.

## Structure
An intro line, then one block per deliverable that has pipelined work:
- `## d-<id> — <Deliverable title>` — must exist in project-prd.md.
- under it, numbered waves: `1. <glyph> **w-<id>** — <Wave title>`.

## Template
```markdown
# Roadmap

SuperMe's per-repo dev index — the work in motion *through SuperMe*, by deliverable. Forward-only;
what's already built lives in [architecture.md](architecture.md) + git.

Legend: ✓ done · ▸ active · · planned.

## d-<id> — <Deliverable title>
1. ▸ **w-<id>** — <Wave title>
2. · **w-<id>** — <Next wave>
```

## Rules
- **Forward-only — no history.** SuperMe didn't build the past, so it isn't recorded here. A retrofit
  roadmap starts with active/next waves only.
- **Roadmap → PRD, never reverse.** Every `## d-<id>` must exist in project-prd.md. A deliverable may
  have no waves yet; a wave may **never** point at a deliverable the PRD doesn't define.
- **Wave status is a curated glyph** (`✓ done` / `▸ active` / `· planned`) — your judgment, not derived.
- **Never list work-items.** They attach via their own `wave: <w-id>` pointer; the board renders each
  wave's live items + rollup + dates. Rollups and dates are computed, never written here.
- **No frontmatter.**
