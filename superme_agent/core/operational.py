"""Operational-learning artifacts on disk (WI-8) — read/assemble + write/publish.

The self-learning loop publishes OPERATIONAL content (constitution / skill / agent) into the
operational tree (`harness/` universal, `local-harness/<id>/<mode>/` per-repo). This module is the
file layer for those artifacts:

- **constitution** — one file per item (frontmatter carries `enabled`), assembled into the system
  prompt every turn for the active scope×mode (always-on). Universal home: `harness/constitution/
  <mode>/`; per-repo home: `local-harness/<id>/<mode>/constitution/`.
- **skill / agent** — files inside the operational *plugin* (universal `superme-dev`; per-repo
  `local-harness/<id>/<mode>` with a bootstrapped manifest); they load via the plugin channel, not
  the prompt.

Disk holds only PUBLISHED, live artifacts (drafts stage in the DB — see dev_store). See
general_docs/learning-workflow-spec.md.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

_FM = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split `---\\nkey: val\\n---\\nbody` into (meta, body). Tolerant: no/blank frontmatter → ({}, text).
    Values are read as plain strings (we only need scalars: enabled/scope/source/name/created)."""
    m = _FM.match(text or "")
    if not m:
        return {}, (text or "")
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, m.group(2).strip()


def set_frontmatter_field(path: Path, key: str, value: str) -> None:
    """Update (or insert) a single scalar frontmatter `key: value` in a `.md`, preserving every
    other line + the body verbatim (line-level edit, not a re-dump). Used to two-way-sync an agent's
    `model:` from the config UI into its own `.md` — the artifact stays the source of truth."""
    text = path.read_text()
    m = _FM.match(text)
    if not m:  # no frontmatter yet — prepend a minimal block
        path.write_text(f"---\n{key}: {value}\n---\n\n{text}")
        return
    lines = m.group(1).splitlines()
    for i, line in enumerate(lines):
        if line.partition(":")[0].strip() == key:
            lines[i] = f"{key}: {value}"
            break
    else:
        lines.append(f"{key}: {value}")
    path.write_text(f"---\n{chr(10).join(lines)}\n---\n{m.group(2)}")


def with_frontmatter_default(text: str, key: str, value: str) -> str:
    """Return `text` with `key: value` ensured in its frontmatter — inserted only if absent (an
    existing value is left untouched). Used to give every forged agent a default `effort` field."""
    m = _FM.match(text or "")
    if not m:
        return f"---\n{key}: {value}\n---\n\n{text or ''}"
    keys = [ln.partition(":")[0].strip() for ln in m.group(1).splitlines()]
    if key in keys:
        return text
    return f"---\n{m.group(1)}\n{key}: {value}\n---\n{m.group(2)}"


def _read_plugin(plugin_dir: Path) -> dict:
    """One universal plugin's SuperMe-authored skills + agents, read from their frontmatter.
    Skills live at `<plugin>/skills/<name>/SKILL.md`, agents at `<plugin>/agents/<name>.md`."""
    skills: list[dict] = []
    agents: list[dict] = []
    sk = plugin_dir / "skills"
    if sk.is_dir():
        for p in sorted(sk.glob("*/SKILL.md")):
            meta, _ = parse_frontmatter(p.read_text())
            skills.append({"kind": "skill", "name": meta.get("name") or p.parent.name,
                           "description": meta.get("description") or "",
                           "category": meta.get("category") or None,
                           "access": meta.get("access") or None})
    ag = plugin_dir / "agents"
    if ag.is_dir():
        for p in sorted(ag.glob("*.md")):
            if p.name.upper() == "README.MD":
                continue
            meta, _ = parse_frontmatter(p.read_text())
            agents.append({"kind": "agent", "name": meta.get("name") or p.stem,
                           "description": meta.get("description") or "",
                           "category": meta.get("category") or None,
                           "model": meta.get("model") or None,
                           "tools": meta.get("tools") or None})
    return {"skills": skills, "agents": agents}


def resolve_plugin_file(scope: str, kind: str, name: str, *,
                        dev_dir: Path, core_dir: Path, shared_dir: Path,
                        local_dir: Path | None = None) -> Path | None:
    """Map (scope, kind, name) → the on-disk artifact path, or None if it doesn't resolve safely.
    Path-traversal-safe: `name` may not contain separators, and the result must sit inside the
    scope's plugin dir. `kind` is 'skill' (`skills/<name>/SKILL.md`) or 'agent' (`agents/<name>.md`).
    `scope='local'` (with `local_dir` = a host's `local-harness/<id>/<mode>`) resolves that host's own
    plugin tree."""
    base = {"dev": dev_dir, "core": core_dir, "shared": shared_dir, "local": local_dir}.get(scope)
    if base is None:
        return None
    name = (name or "").strip()
    if not name or any(c in name for c in ("/", "\\", "..")):
        return None
    if kind == "skill":
        p = (base / "skills" / name / "SKILL.md").resolve()
    elif kind == "agent":
        p = (base / "agents" / f"{name}.md").resolve()
    else:
        return None
    return p if base.resolve() in p.parents else None


def _plugin_namespace(plugin_dir: Path) -> str:
    """A plugin's slash-command namespace = its manifest `name` (falls back to the dir name)."""
    mf = Path(plugin_dir) / ".claude-plugin" / "plugin.json"
    if mf.is_file():
        try:
            import json
            return json.loads(mf.read_text()).get("name") or Path(plugin_dir).name
        except Exception:
            return Path(plugin_dir).name
    return Path(plugin_dir).name


def list_palette_skills(plugin_dirs: list[Path]) -> list[dict]:
    """Every skill across these plugin dirs as `{command, category, namespace}` — `command` is the
    `<namespace>:<skill>` slug the chat "/" palette shows. Disabled skills (moved to `.disabled/`) are
    naturally excluded (only `skills/<name>/SKILL.md` is scanned). The caller filters by category and
    merges native commands."""
    out: list[dict] = []
    for d in plugin_dirs:
        p = Path(d)
        if not p.is_dir():
            continue
        ns = _plugin_namespace(p)
        for sk in _read_plugin(p)["skills"]:
            out.append({"command": f"{ns}:{sk['name']}", "category": sk.get("category"), "namespace": ns})
    return out


def silent_skill_names(plugin_dirs: list[Path]) -> set[str]:
    """Skills marked `access: silent` across these plugin dirs — internal machinery that the
    user-facing turn must never invoke directly (only their owning sub-run may). Returns BOTH the
    namespaced `<ns>:<name>` and the bare `<name>` so a permission check can match either form."""
    out: set[str] = set()
    for d in plugin_dirs:
        p = Path(d)
        if not p.is_dir():
            continue
        ns = _plugin_namespace(p)
        for sk in _read_plugin(p)["skills"]:
            if (sk.get("access") or "").strip().lower() == "silent":
                out.add(f"{ns}:{sk['name']}")
                out.add(sk["name"])
    return out


def skills_in_category(plugin_dirs: list[Path], category: str) -> set[str]:
    """Skill names in `category` across these plugin dirs, in BOTH the namespaced `<ns>:<name>` and
    bare `<name>` forms (same contract as `silent_skill_names` — a permission check may see either).

    Unlike `access: silent` (which is a permanent property of a skill), a category block is a
    property of the SESSION: the caller decides when a category is off-limits. Today that's the
    `onboarding` category — project-init/retrofit exist to establish a project's memory, so once it
    IS established they can only do harm (retrofit re-derives the anchor docs from the code and
    would overwrite the owner's approved ones). They're one-shot per repo, and nothing else expires
    like that, so the kernel — not the skill's own prose — is what enforces it."""
    out: set[str] = set()
    want = (category or "").strip().lower()
    for d in plugin_dirs:
        p = Path(d)
        if not p.is_dir():
            continue
        ns = _plugin_namespace(p)
        for sk in _read_plugin(p)["skills"]:
            if (sk.get("category") or "").strip().lower() == want:
                out.add(f"{ns}:{sk['name']}")
                out.add(sk["name"])
    return out


def list_harness_plugins(*, dev_dir: Path, core_dir: Path, shared_dir: Path) -> list[dict]:
    """SuperMe's OWN universal skills/agents, grouped by the scope that loads them: `dev` and
    `core` (mode-selected) plus `shared` (loaded in every mode). The per-repo operational tree
    (`local-harness/<id>/<mode>`) is deliberately excluded — this is the universal harness only."""
    return [
        {"scope": "dev", "label": "Dev", "plugin": "superme-dev",
         "note": "Loaded in dev mode", **_read_plugin(dev_dir)},
        {"scope": "core", "label": "Core", "plugin": "superme-core",
         "note": "Loaded in core mode", **_read_plugin(core_dir)},
        {"scope": "shared", "label": "Shared", "plugin": "superme-shared",
         "note": "Loaded in every mode", **_read_plugin(shared_dir)},
    ]


def _is_enabled(meta: dict) -> bool:
    """`enabled` defaults to True when absent (an item with no flag is live)."""
    v = str(meta.get("enabled", "true")).strip().lower()
    return v not in ("false", "0", "no", "off")


def _is_foundational(meta: dict) -> bool:
    """`foundational: true` marks a constitution a charter consults BY NAME (e.g. dev-knowledge-structure).
    Disabling one would dangle the charter's pull, so the toggle refuses it — it's pinned always-on."""
    return str(meta.get("foundational", "false")).strip().lower() in ("true", "1", "yes", "on")


def _title_of(body: str, slug: str) -> str:
    """The artifact's own H1 if it has one, else the slug read as words. The H1 is what the
    author named the thing, so it keeps real hyphens ('Dev-knowledge structure') that a blanket
    dash-to-space rule on the slug would flatten. One writer: every surface reads this key."""
    for line in body.splitlines():
        t = line.strip()
        if t.startswith("# "):
            return t[2:].strip()
        if t and not t.startswith("#"):
            break                       # prose before any heading → the file has no H1
    words = slug.replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def read_constitution_dir(directory: Path, *, origin: str) -> list[dict]:
    """Read one constitution home into a list of items (newest filename last → stable order).
    `origin` tags where it came from ('universal' | 'repo'). Missing dir → []."""
    out: list[dict] = []
    d = Path(directory)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):
        if p.name.upper() in ("README.MD",):
            continue
        meta, body = parse_frontmatter(p.read_text())
        if not body.strip():
            continue
        slug = meta.get("name") or p.stem
        out.append({
            "slug": slug,
            "title": _title_of(body, slug),  # display name; see _title_of
            "enabled": _is_enabled(meta),
            "foundational": _is_foundational(meta),  # charter-pinned → not disable-able
            "description": meta.get("description"),  # the always-resident catalog line (directive / when-to-apply)
            "scope": meta.get("scope"),
            "source": meta.get("source"),
            "created": meta.get("created"),
            "updated": meta.get("updated"),  # last-edited stamp; evolving constitutions bump it
            "origin": origin,
            "path": str(p),
            "body": body.strip(),
        })
    return out


# --- ASSET pool (opt-in constitutional knowledge, activated per repo) ------------------------
# `local-harness/asset/` (config.ASSET_DIR) holds constitutional KNOWLEDGE (e.g. sql-expert) that is
# NOT universal — any repo can EQUIP it for itself. A repo activates slugs by reference (no body copy):
# the active set is a plain slug list at `<repo constitution home>/.assets`. Default empty ⇒ a new repo
# carries zero assets. One shared pool, all repos draw from it. (Distinct from the plugins'
# doc-authoring `references/`, which are the "how to write each general doc" guides.)


def read_asset_pool(asset_dir: Path | None = None) -> list[dict]:
    """The asset pool: opt-in items in the shared `local-harness/asset/`, tagged origin='asset'."""
    from ..paths import ASSET_DIR
    return read_constitution_dir(Path(asset_dir or ASSET_DIR), origin="asset")


def _repo_asset_file(repo_dir: Path) -> Path:
    """The adopted-asset list for a repo — a `.assets` file in its constitution home."""
    return Path(repo_dir) / ".assets"


def repo_asset_states(repo_dir: Path | None) -> dict[str, bool]:
    """The asset-pool items a repo has ADOPTED → {slug: enabled}. `.assets` lines are `slug`
    (adopted + enabled) or `slug  # off` (adopted but disabled). Empty / no file ⇒ {}."""
    if repo_dir is None:
        return {}
    f = _repo_asset_file(repo_dir)
    if not f.is_file():
        return {}
    states: dict[str, bool] = {}
    for ln in f.read_text().splitlines():
        s = ln.strip()
        if not s:
            continue
        slug = s.split("#", 1)[0].strip()
        if slug:
            states[slug] = "# off" not in s
    return states


def _write_asset_states(repo_dir: Path, states: dict[str, bool]) -> None:
    """Serialize the adopted map back to `.assets` (creating the repo home if needed)."""
    f = _repo_asset_file(repo_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    lines = [slug if en else f"{slug}  # off" for slug, en in sorted(states.items())]
    f.write_text(("\n".join(lines) + "\n") if lines else "")


def list_repo_assets(repo_dir: Path | None) -> set[str]:
    """ENABLED asset slugs for a repo — the set that drives the constitution catalog / context build.
    Adopted-but-disabled items are excluded."""
    return {slug for slug, en in repo_asset_states(repo_dir).items() if en}


def set_repo_asset(repo_dir: Path | None, slug: str, enabled: bool) -> dict[str, bool]:
    """Enable/disable one adopted asset (adopting it first if absent — this backs the manual `+ Add`
    and the enable/disable toggle). Disabling KEEPS adoption. Returns the new adopted map. No body
    is ever copied."""
    if repo_dir is None:
        return {}
    states = repo_asset_states(repo_dir)
    states[slug] = enabled
    _write_asset_states(repo_dir, states)
    return states


def adopt_repo_assets(repo_dir: Path | None, slugs: list[str]) -> list[str]:
    """Bulk-adopt (enabled) any not-yet-adopted slugs — the onboarding auto-adopt. Already-adopted
    items (including ones the owner disabled) are left untouched. Returns the newly adopted slugs."""
    if repo_dir is None:
        return []
    states = repo_asset_states(repo_dir)
    newly = [s for s in slugs if s not in states]
    for s in newly:
        states[s] = True
    if newly:
        _write_asset_states(repo_dir, states)
    return newly


def drop_repo_asset(repo_dir: Path | None, slug: str) -> dict[str, bool]:
    """Un-adopt one asset entirely (remove its line) — the `Drop` action. Returns the new map."""
    if repo_dir is None:
        return {}
    states = repo_asset_states(repo_dir)
    states.pop(slug, None)
    _write_asset_states(repo_dir, states)
    return states


def _activated_asset_items(activated: set[str] | None, asset_dir: Path | None = None) -> list[dict]:
    """Asset-pool items a repo has ENABLED (and that aren't globally killed via `enabled`)."""
    if not activated:
        return []
    return [it for it in read_asset_pool(asset_dir) if it["slug"] in activated]


def list_constitution(mode: str, universal_dir: Path, repo_dir: Path | None, *,
                      activated: set[str] | None = None, asset_dir: Path | None = None) -> list[dict]:
    """All constitution items in a repo's scope: universal (applies everywhere) + this repo's
    authored + the ASSET-pool items it has ACTIVATED. Includes disabled items (the manage UI needs
    them); callers filter on `enabled` as needed. `activated` is the repo's active asset-slug set;
    `asset_dir` overrides the shared pool location (tests)."""
    items = read_constitution_dir(universal_dir, origin="universal")
    if repo_dir is not None:
        items += read_constitution_dir(repo_dir, origin="repo")
    items += _activated_asset_items(activated, asset_dir)
    return items


def constitution_catalog(mode: str, universal_dir: Path, repo_dir: Path | None, *,
                         activated: set[str] | None = None, asset_dir: Path | None = None) -> str:
    """The always-on constitution CATALOG: one frontmatter line per ENABLED in-scope item
    (universal + this repo's authored + its activated ASSET-pool items) — name + its self-sufficient
    description. Bodies are NOT dumped; the agent pulls a body on demand via `pull_constitution(name)`.
    Empty string when there are none. Frontmatter-first loading model (context-model-spec §1/§2): the
    directive/when-to-apply lives in the always-resident description.
    """
    items = [it for it in list_constitution(mode, universal_dir, repo_dir,
                                            activated=activated, asset_dir=asset_dir)
             if it["enabled"]]
    if not items:
        return ""
    lines = []
    for it in items:
        desc = (it.get("description") or "").strip() or "(no description — pull to read)"
        lines.append(f"- **{it['slug']}** — {desc}")
    header = (
        "## Constitution catalog (operational directives — in force)\n"
        "These are the list of some constitution of superme's framework. Each line names one constitution; when its "
        "description is relevant to what you're doing and you need the full contract or information, call "
        "`pull_constitution(name)` to load its body and follow them."
    )
    return header + "\n\n" + "\n".join(lines)


# Tiny stopword set for the relevance ranker — enough to keep matches meaningful without a
# dependency. Not exhaustive; the goal is signal, not linguistics.
_STOP = frozenset((
    "the a an and or of to in on for with is are be this that these those it its as at by from "
    "you your we our they their he she i me my not no do does done can will would should may might "
    "use used using when where what which who how why into over under out up down off no yes than "
    "then them so if but also more most some any all each per via etc eg ie about across only just"
).split())


def _terms(text: str) -> set[str]:
    """Lowercased word set, length ≥ 3, minus stopwords — the unit the relevance score compares."""
    import re
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 3 and w not in _STOP}


def rank_assets_by_relevance(
    spec_text: str, activated: set[str] | None = None, *, asset_dir: Path | None = None, limit: int = 8,
) -> list[dict]:
    """Rank the ASSET POOL by keyword overlap with `spec_text` — the spec→asset bridge: a project's
    stated stack/approach surfaces the knowledge assets it implies, which the owner can then activate
    for this repo. Deterministic (no embeddings): a term shared with an item's slug or description
    (the signal-dense catalog line) counts double vs. one only in its body. Items with zero overlap
    are dropped. Each result carries `activated` (already on for this repo) so the caller can
    foreground the relevant-but-not-yet-active ones. Read-only — activating stays the owner's gate."""
    want = _terms(spec_text)
    if not want:
        return []
    active = activated or set()
    ranked: list[dict] = []
    for it in read_asset_pool(asset_dir):
        head = _terms(f"{it['slug']} {it.get('description') or ''}")
        body = _terms(it.get("body") or "")
        hits = want & (head | body)
        if not hits:
            continue
        score = 2 * len(want & head) + len(want & (body - head))
        ranked.append({
            "slug": it["slug"], "description": it.get("description"),
            "activated": it["slug"] in active, "score": score,
            "confident": bool(want & head),  # matched the slug/description, not merely the body
            "matched": sorted(want & head | (want & body), key=lambda t: (t not in head, t))[:6],
        })
    ranked.sort(key=lambda r: r["score"], reverse=True)
    return ranked[:limit]


def resolve_constitution(mode: str, universal_dir: Path, repo_dir: Path | None, name: str, *,
                         activated: set[str] | None = None, asset_dir: Path | None = None) -> dict | None:
    """Find one ENABLED in-scope constitution by name (slug), or None. Backs the `pull_constitution`
    tool — scope is enforced by the dirs/active-set the caller passes (universal + repo + activated
    ASSET items), so an out-of-scope or un-activated item is simply not found."""
    want = (name or "").strip().lower()
    for it in list_constitution(mode, universal_dir, repo_dir, activated=activated, asset_dir=asset_dir):
        if it["enabled"] and it["slug"].strip().lower() == want:
            return it
    return None


# --------------------------------------------------------------------------- publish (gate-2)
# `target_scope` encodes BOTH the universal-vs-repo axis AND the mode: repo_dev / universal_dev are
# dev-mode operational content; `core` is reserved (core-mode internals deferred, §4.11.6 #3).

class ReservedScope(Exception):
    """Raised when an apply targets a not-yet-built home (core)."""


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "item"


def _homes():
    # Imported lazily to keep this module's import graph light + avoid any cycle.
    from ..paths import CONSTITUTION_DIR, LOCAL_HARNESS_DIR, DEV_PLUGIN_DIR
    return CONSTITUTION_DIR, LOCAL_HARNESS_DIR, DEV_PLUGIN_DIR


def constitution_home(target_scope: str, repo_id: str | None) -> Path:
    """Where a constitution item of this scope lives (one-file-per-item dir)."""
    CONST, LOCAL, _ = _homes()
    if target_scope == "universal_dev":
        return CONST / "dev"
    if target_scope == "repo_dev":
        if not repo_id:
            raise ValueError("repo_dev constitution needs a repo_id")
        return LOCAL / repo_id / "dev" / "constitution"
    raise ReservedScope(f"constitution apply for scope '{target_scope}' is reserved")


def plugin_root(target_scope: str, repo_id: str | None) -> Path:
    """The plugin root that holds skills/ + agents/ for this scope (universal = the shipped
    superme-dev plugin; repo = the per-repo operational cell, manifest bootstrapped on write)."""
    CONST, LOCAL, DEV_PLUGIN = _homes()
    if target_scope == "universal_dev":
        return DEV_PLUGIN
    if target_scope == "repo_dev":
        if not repo_id:
            raise ValueError("repo_dev skill/agent needs a repo_id")
        return LOCAL / repo_id / "dev"
    raise ReservedScope(f"skill/agent apply for scope '{target_scope}' is reserved")


def ensure_plugin_manifest(root: Path, name: str) -> None:
    """Bootstrap a minimal Claude-Code plugin manifest so a per-repo operational cell's skills/agents
    load. No-op if one already exists (the universal plugin ships with its own)."""
    manifest = Path(root) / ".claude-plugin" / "plugin.json"
    if manifest.is_file():
        return
    manifest.parent.mkdir(parents=True, exist_ok=True)
    import json
    manifest.write_text(json.dumps(
        {"name": name, "description": f"Per-repo operational plugin ({name})", "version": "0.0.1"},
        indent=2) + "\n")


def publish_artifact(output_form: str, target_scope: str, repo_id: str | None, *,
                     slug: str, content: str, source: str = "agent",
                     created: str = "") -> str:
    """Gate-2 publish — write a PUBLISHED operational artifact to its live home and return the path.
    `content` is the write phase's final artifact, frontmatter-first for every form: constitution
    carries a `description` (+ optional body); skill/agent are the complete SKILL.md / agent.md. The
    server-side fields (name, runtime `enabled`, scope, provenance) are stamped here as defaults —
    never clobbering what the agent authored. Every form is stamped `category: learned`. Raises
    ReservedScope for `core`."""
    slug = slugify(slug)
    if output_form == "constitution":
        home = constitution_home(target_scope, repo_id)
        home.mkdir(parents=True, exist_ok=True)
        # Stamp the server-side frontmatter onto the agent's frontmatter-first artifact (the
        # `description` + optional body it authored), same with_frontmatter_default path skill/agent
        # use. `enabled` frontmatter lets runtime on/off be a flag flip, not a delete.
        content = with_frontmatter_default(content, "name", slug)
        content = with_frontmatter_default(content, "enabled", "true")
        content = with_frontmatter_default(content, "scope", target_scope)
        if source:
            content = with_frontmatter_default(content, "source", source)
        if created:
            content = with_frontmatter_default(content, "created", created)
            content = with_frontmatter_default(content, "updated", created)
        content = with_frontmatter_default(content, "category", "learned")
        path = home / f"{slug}.md"
        path.write_text(content if content.endswith("\n") else content + "\n")
        return str(path)
    if output_form in ("skill", "agent"):
        root = plugin_root(target_scope, repo_id)
        if target_scope == "repo_dev":
            ensure_plugin_manifest(root, f"{repo_id}-dev")
        # Every learned artifact is stamped `category: learned` — the provenance marker that
        # separates learning-loop output (visible) from shipped machinery (`category: learning`,
        # hidden from the palette). Injected only if the forge phase didn't already set a category.
        content = with_frontmatter_default(content, "category", "learned")
        if output_form == "skill":
            path = root / "skills" / slug / "SKILL.md"
        else:
            path = root / "agents" / f"{slug}.md"
            # Every forged agent gets a default reasoning effort (owner-tunable later); a background
            # runner reads this field. Injected only if the forge phase didn't already set one.
            content = with_frontmatter_default(content, "effort", "medium")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if content.endswith("\n") else content + "\n")
        return str(path)
    raise ValueError(f"unknown output_form: {output_form}")


# --------------------------------------------------------------------------- runtime management (#6)
# Post-publish, the owner governs live artifacts. Constitution carries an `enabled` frontmatter flag
# the loader already honors (the catalog + pull_constitution filter on it). Skills/agents load via Claude
# Code's OWN plugin scanner, which only looks in `<plugin>/skills/` + `<plugin>/agents/` and ignores
# frontmatter — so "disable" there means moving the artifact into a `.disabled/` shadow at the plugin
# root (a sibling the scanner never descends into). Enable moves it back; delete removes it outright.

_DISABLED = ".disabled"


def _plugin_artifact_paths(root: Path, kind: str, slug: str) -> tuple[Path, Path]:
    """(live, disabled) paths for a skill/agent under a plugin root. A skill is a directory
    (`skills/<slug>/SKILL.md`), an agent a single file (`agents/<slug>.md`); the `.disabled/` shadow
    mirrors that layout so a move round-trips cleanly."""
    if kind == "skill":
        return root / "skills" / slug / "SKILL.md", root / _DISABLED / "skills" / slug / "SKILL.md"
    return root / "agents" / f"{slug}.md", root / _DISABLED / "agents" / f"{slug}.md"


def _published_paths(form: str, scope: str, repo_id: str | None, slug: str) -> dict:
    """Resolve a published artifact's on-disk home(s). Constitution → one file; skill/agent → the
    (live, disabled) pair. Slug is normalized the same way publish did."""
    slug = slugify(slug)
    if form == "constitution":
        return {"file": constitution_home(scope, repo_id) / f"{slug}.md"}
    if form in ("skill", "agent"):
        live, shadow = _plugin_artifact_paths(plugin_root(scope, repo_id), form, slug)
        return {"live": live, "disabled": shadow}
    raise ValueError(f"unknown form: {form}")


def published_state(form: str, scope: str, repo_id: str | None, slug: str) -> dict:
    """Live on-disk state of a published artifact: `present` (still on disk at all) + `enabled`.
    Constitution reads its frontmatter flag; skill/agent infer from which tree the file sits in."""
    p = _published_paths(form, scope, repo_id, slug)
    if form == "constitution":
        f = p["file"]
        if not f.is_file():
            return {"present": False, "enabled": False}
        meta, _ = parse_frontmatter(f.read_text())
        return {"present": True, "enabled": _is_enabled(meta)}
    if p["live"].is_file():
        return {"present": True, "enabled": True}
    if p["disabled"].is_file():
        return {"present": True, "enabled": False}
    return {"present": False, "enabled": False}


def _flip_constitution(path: Path, enabled: bool) -> None:
    """Rewrite a constitution file's `enabled` frontmatter flag in place (insert it if absent)."""
    text = path.read_text()
    val = "true" if enabled else "false"
    m = _FM.match(text)
    if not m:  # no frontmatter — wrap minimally so the flag has a home
        path.write_text(f"---\nenabled: {val}\n---\n{text.strip()}\n")
        return
    fm, body = m.group(1), m.group(2)
    if re.search(r"(?m)^enabled:.*$", fm):
        fm = re.sub(r"(?m)^enabled:.*$", f"enabled: {val}", fm)
    else:
        fm = fm.rstrip("\n") + f"\nenabled: {val}"
    path.write_text(f"---\n{fm}\n---\n{body}")


def set_published_enabled(form: str, scope: str, repo_id: str | None, slug: str,
                          enabled: bool) -> dict | None:
    """Toggle a published artifact without deleting it; returns the new state, or None if absent.
    Constitution → frontmatter flip. Skill/agent → move the artifact between the live plugin tree and
    its `.disabled/` shadow. Idempotent. Effective on the next dev turn (plugins re-read per turn)."""
    st = published_state(form, scope, repo_id, slug)
    if not st["present"]:
        return None
    if st["enabled"] == enabled:
        return st
    p = _published_paths(form, scope, repo_id, slug)
    if form == "constitution":
        _flip_constitution(p["file"], enabled)
        return {"present": True, "enabled": enabled}
    src, dst = (p["disabled"], p["live"]) if enabled else (p["live"], p["disabled"])
    if form == "skill":  # the unit is the <slug>/ dir
        src_dir, dst_dir = src.parent, dst.parent
        dst_dir.parent.mkdir(parents=True, exist_ok=True)
        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        shutil.move(str(src_dir), str(dst_dir))
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        shutil.move(str(src), str(dst))
    return {"present": True, "enabled": enabled}


def published_file(form: str, scope: str, repo_id: str | None, slug: str) -> Path | None:
    """The editable markdown file for a published artifact (constitution → its file; skill/agent →
    whichever of the live/disabled copies is on disk). None if nothing is present."""
    p = _published_paths(form, scope, repo_id, slug)
    if form == "constitution":
        f = p["file"]
        return f if f.is_file() else None
    for art in (p["live"], p["disabled"]):
        if art.is_file():
            return art
    return None


def delete_published(form: str, scope: str, repo_id: str | None, slug: str) -> bool:
    """Remove a published artifact from disk entirely — both the live copy and any `.disabled/`
    shadow. Returns True if anything was removed. The proposal row stays as history (caller retires
    it). The loader stops seeing it next turn."""
    p = _published_paths(form, scope, repo_id, slug)
    if form == "constitution":
        if p["file"].is_file():
            p["file"].unlink()
            return True
        return False
    removed = False
    for art in (p["live"], p["disabled"]):
        if form == "skill":
            if art.parent.is_dir():
                shutil.rmtree(art.parent)
                removed = True
        elif art.is_file():
            art.unlink()
            removed = True
    return removed
