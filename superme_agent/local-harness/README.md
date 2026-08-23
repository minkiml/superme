# local-harness/ — PER-REPO operational elements

**Operational** = anything that *determines how the agent behaves* (capability, pattern, style,
guardrails, identity). It is **pinned, governed, and lives with the code** (committed in the app
repo) — as opposed to *knowledge* (behavior-neutral substrate), which lives in the separate
`superme-knowledge/` repo and is pulled on demand.

This tree is the **per-repo, additive** operational layer. The UNIVERSAL operational elements
(sharable anywhere) live in `../harness/` (persona · policies · core/dev charters · the
`superme-{shared,core,dev}` plugins). `local-harness/<repo>/<scope>/` layers repo-specialized
operational elements **on top**, loaded only when the agent runs in that repo × scope.

```
local-harness/
├─ global/        the global "SuperMe hub" repo
│  ├─ core/       operational elements active in global × core
│  └─ dev/        operational elements active in global × dev
└─ your-repo/     any repo you connect
   ├─ core/
   └─ dev/
```

Each `<scope>/` will hold the self-learned + user-added operational products of the gated learning
path — `{constitution, skill, agent}` (one coarse `constitution` bucket for all identity / principles
/ rules / conventions / contracts; no sub-types yet).

> **Status: placeholder (relocation pending).** Today the per-repo procedural cell still lives at
> `<knowledge-home>/practice/<mode>` (loaded via `paths.plugins_for`). The renovation's
> relocation pass moves it here, and `config.py` / `agent_service.py` path resolution switches to
> this tree. The empty `.gitkeep`s mark the target shape; nothing loads from here yet.

