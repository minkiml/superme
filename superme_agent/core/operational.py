"""Operational-learning artifacts on disk — read, assemble, publish.

`constitution` is one file per item, assembled into the system prompt every turn. `skill`/`agent`
load via the plugin channel. Disk holds only published artifacts; drafts stage in `dev_store`.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

_FM = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split `---\nkey: val\n---\nbody` into (meta, body). Tolerant: no frontmatter
    → ({}, text). Scalars only."""
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
    """Update or insert one scalar frontmatter field, preserving every other
    line and the body."""
    text = path.read_text(encoding="utf-8")
    m = _FM.match(text)
    if not m:  # no frontmatter yet — prepend a minimal block
        path.write_text(f"---\n{key}: {value}\n---\n\n{text}", encoding="utf-8")
        return
    lines = m.group(1).splitlines()
    for i, line in enumerate(lines):
        if line.partition(":")[0].strip() == key:
            lines[i] = f"{key}: {value}"
            break
    else:
        lines.append(f"{key}: {value}")
    path.write_text(f"---\n{chr(10).join(lines)}\n---\n{m.group(2)}", encoding="utf-8")


def with_frontmatter_default(text: str, key: str, value: str) -> str:
    """Ensure `key: value` in the frontmatter, inserted only if absent.
    An existing value is untouched."""
    m = _FM.match(text or "")
    if not m:
        return f"---\n{key}: {value}\n---\n\n{text or ''}"
    keys = [ln.partition(":")[0].strip() for ln in m.group(1).splitlines()]
    if key in keys:
        return text
    return f"---\n{m.group(1)}\n{key}: {value}\n---\n{m.group(2)}"


def read_plugin(plugin_dir: Path) -> dict:
    """One universal plugin's SuperMe-authored skills + agents, read from their frontmatter.
    Skills live at `<plugin>/skills/<name>/SKILL.md`, agents at `<plugin>/agents/<name>.md`."""
    skills: list[dict] = []
    agents: list[dict] = []
    sk = plugin_dir / "skills"
    if sk.is_dir():
        for p in sorted(sk.glob("*/SKILL.md")):
            meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
            skills.append({"kind": "skill", "name": meta.get("name") or p.parent.name,
                           "description": meta.get("description") or "",
                           "category": meta.get("category") or None,
                           "access": meta.get("access") or None})
    ag = plugin_dir / "agents"
    if ag.is_dir():
        for p in sorted(ag.glob("*.md")):
            if p.name.upper() == "README.MD":
                continue
            meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
            agents.append({"kind": "agent", "name": meta.get("name") or p.stem,
                           "description": meta.get("description") or "",
                           "category": meta.get("category") or None,
                           "model": meta.get("model") or None,
                           "tools": meta.get("tools") or None})
    return {"skills": skills, "agents": agents}


def resolve_plugin_file(scope: str, kind: str, name: str, *,
                        dev_dir: Path, core_dir: Path, shared_dir: Path,
                        local_dir: Path | None = None) -> Path | None:
    """Map (scope, kind, name) → the on-disk path, or None. Path-traversal-safe:
    no separators, and the result must stay inside."""
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
            return json.loads(mf.read_text(encoding="utf-8")).get("name") or Path(plugin_dir).name
        except Exception:
            return Path(plugin_dir).name
    return Path(plugin_dir).name


def list_palette_skills(plugin_dirs: list[Path]) -> list[dict]:
    """Every skill across these plugin dirs as `{command, category, namespace}`. Disabled skills
    are naturally excluded, since only `skills/<name>/SKILL.md` is scanned."""
    out: list[dict] = []
    for d in plugin_dirs:
        p = Path(d)
        if not p.is_dir():
            continue
        ns = _plugin_namespace(p)
        for sk in read_plugin(p)["skills"]:
            out.append({"command": f"{ns}:{sk['name']}", "category": sk.get("category"), "namespace": ns})
    return out


def silent_skill_names(plugin_dirs: list[Path]) -> set[str]:
    """Skills marked `access: silent` — only their owning sub-run may invoke them.
    Both namespaced and bare forms."""
    out: set[str] = set()
    for d in plugin_dirs:
        p = Path(d)
        if not p.is_dir():
            continue
        ns = _plugin_namespace(p)
        for sk in read_plugin(p)["skills"]:
            if (sk.get("access") or "").strip().lower() == "silent":
                out.add(f"{ns}:{sk['name']}")
                out.add(sk["name"])
    return out


def skills_in_category(plugin_dirs: list[Path], category: str) -> set[str]:
    """Skill names in `category`, namespaced and bare. Unlike `access: silent`,
    a category block is a property of the session."""
    out: set[str] = set()
    want = (category or "").strip().lower()
    for d in plugin_dirs:
        p = Path(d)
        if not p.is_dir():
            continue
        ns = _plugin_namespace(p)
        for sk in read_plugin(p)["skills"]:
            if (sk.get("category") or "").strip().lower() == want:
                out.add(f"{ns}:{sk['name']}")
                out.add(sk["name"])
    return out


def list_harness_plugins(*, dev_dir: Path, core_dir: Path, shared_dir: Path) -> list[dict]:
    """SuperMe's own universal skills/agents by loading scope: `dev`, `core`,
    `shared`. Per-repo trees are deliberately excluded."""
    return [
        {"scope": "dev", "label": "Dev", "plugin": "superme-dev",
         "note": "Loaded in dev mode", **read_plugin(dev_dir)},
        {"scope": "core", "label": "Core", "plugin": "superme-core",
         "note": "Loaded in core mode", **read_plugin(core_dir)},
        {"scope": "shared", "label": "Shared", "plugin": "superme-shared",
         "note": "Loaded in every mode", **read_plugin(shared_dir)},
    ]


def _is_enabled(meta: dict) -> bool:
    """`enabled` defaults to True when absent (an item with no flag is live)."""
    v = str(meta.get("enabled", "true")).strip().lower()
    return v not in ("false", "0", "no", "off")


def is_foundational(meta: dict) -> bool:
    """`foundational: true` marks a constitution a charter consults by name.
    Disabling one would dangle that pull."""
    return str(meta.get("foundational", "false")).strip().lower() in ("true", "1", "yes", "on")


def _title_of(body: str, slug: str) -> str:
    """The artifact's own H1 if it has one, else the slug as words. The H1 keeps real hyphens."""
    for line in body.splitlines():
        t = line.strip()
        if t.startswith("# "):
            return t[2:].strip()
        if t and not t.startswith("#"):
            break                       # prose before any heading → the file has no H1
    words = slug.replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def is_hub_only(meta: dict) -> bool:
    """`hub-only: true` restricts a shelf item to the engine's own cell."""
    v = meta.get("hub-only", meta.get("hub_only", "false"))
    return str(v).strip().lower() in ("true", "1", "yes", "on")


def read_constitution_dir(directory: Path, *, origin: str,
                          recursive: bool = False) -> list[dict]:
    """Read one constitution home into a list of items. `origin` tags where it
    came from. Missing dir → []."""
    out: list[dict] = []
    d = Path(directory)
    if not d.is_dir():
        return out
    for p in sorted(d.rglob("*.md") if recursive else d.glob("*.md")):
        if p.name.upper() in ("README.MD",):
            continue
        meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
        if not body.strip():
            continue
        slug = meta.get("name") or p.stem
        out.append({
            "slug": slug,
            "title": _title_of(body, slug),
            "enabled": _is_enabled(meta),
            "foundational": is_foundational(meta),  # charter-pinned → not disable-able
            "description": meta.get("description"),  # the always-resident catalog line (directive / when-to-apply)
            "hub_only": is_hub_only(meta),          # the engine's own cell may see it, no other repo
            "source": meta.get("source"),
            "created": meta.get("created"),
            "updated": meta.get("updated"),
            "origin": origin,
            "path": str(p),
            "body": body.strip(),
        })
    return out


# A repo activates slugs by reference in `.assets` — no body is ever copied.


HUB_CONTEXT_ID = "global"


def read_asset_pool(asset_dir: Path | None = None) -> list[dict]:
    """The shared shelf, read as a tree. A slug is the filename, never the path."""
    from ..paths import ASSET_DIR
    return read_constitution_dir(Path(asset_dir or ASSET_DIR), origin="asset", recursive=True)


def is_hub_home(repo_dir: Path | None) -> bool:
    """True when this constitution home belongs to the engine's own cell."""
    from ..paths import LOCAL_HARNESS_DIR
    if repo_dir is None:
        return False
    try:
        rel = Path(repo_dir).resolve().relative_to(Path(LOCAL_HARNESS_DIR).resolve())
    except (ValueError, OSError):
        return False
    return rel.parts[:1] == (HUB_CONTEXT_ID,)


def available_assets(repo_dir: Path | None, asset_dir: Path | None = None) -> list[dict]:
    """Shelf items this repo may search, adopt and read. Globally on, and not restricted away."""
    hub = is_hub_home(repo_dir)
    return [it for it in read_asset_pool(asset_dir)
            if it["enabled"] and (hub or not it["hub_only"])]


def _repo_asset_file(repo_dir: Path) -> Path:
    """The adopted-asset list for a repo — a `.assets` file in its constitution home."""
    return Path(repo_dir) / ".assets"


def repo_asset_states(repo_dir: Path | None) -> dict[str, bool]:
    """The asset-pool items a repo has adopted → {slug: enabled}. `.assets` lines
    are `slug` or `slug  # off`."""
    if repo_dir is None:
        return {}
    f = _repo_asset_file(repo_dir)
    if not f.is_file():
        return {}
    states: dict[str, bool] = {}
    for ln in f.read_text(encoding="utf-8").splitlines():
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
    f.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def list_repo_assets(repo_dir: Path | None) -> set[str]:
    """Enabled asset slugs for a repo. Adopted-but-disabled items are excluded."""
    return {slug for slug, en in repo_asset_states(repo_dir).items() if en}


def set_repo_asset(repo_dir: Path | None, slug: str, enabled: bool) -> dict[str, bool]:
    """Enable/disable one adopted asset, adopting it first if absent. Disabling keeps
    adoption. No body is ever copied."""
    if repo_dir is None:
        return {}
    states = repo_asset_states(repo_dir)
    states[slug] = enabled
    _write_asset_states(repo_dir, states)
    return states


def adopt_repo_assets(repo_dir: Path | None, slugs: list[str], *,
                      asset_dir: Path | None = None) -> list[str]:
    """Bulk-adopt any not-yet-adopted slugs. Already-adopted items, including
    disabled ones, are untouched. Returns the new slugs."""
    if repo_dir is None:
        return []
    allowed = {it["slug"] for it in available_assets(repo_dir, asset_dir)}
    states = repo_asset_states(repo_dir)
    newly = [s for s in slugs if s not in states and s in allowed]
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


def _activated_asset_items(activated: set[str] | None, repo_dir: Path | None = None,
                           asset_dir: Path | None = None) -> list[dict]:
    """Asset-pool items this repo enabled that the shelf still offers."""
    if not activated:
        return []
    return [it for it in available_assets(repo_dir, asset_dir) if it["slug"] in activated]


def list_constitution(mode: str, universal_dir: Path, repo_dir: Path | None, *,
                      activated: set[str] | None = None, asset_dir: Path | None = None) -> list[dict]:
    """Every constitution item in a repo's scope: universal + authored + activated
    assets. Includes disabled ones; callers filter."""
    items = read_constitution_dir(universal_dir, origin="universal")
    if repo_dir is not None:
        items += read_constitution_dir(repo_dir, origin="repo")
    items += _activated_asset_items(activated, repo_dir, asset_dir)
    return items


def constitution_catalog(mode: str, universal_dir: Path, repo_dir: Path | None, *,
                         activated: set[str] | None = None, asset_dir: Path | None = None) -> str:
    """The always-on catalog: one frontmatter line per enabled in-scope item.
    Bodies are pulled on demand via `read_constitution`."""
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
        "## Constitution catalog\n"
        "These are the list of some constitution of superme's framework. Each line names one constitution; when its "
        "description is relevant to what you're doing and you need the full contract or information, call "
        "`read_constitution(name)` to load its body and follow them."
    )
    return header + "\n\n" + "\n".join(lines)


# Enough to keep matches meaningful without a dependency. Signal, not linguistics.
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
    spec_text: str, activated: set[str] | None = None, *, repo_dir: Path | None = None,
    asset_dir: Path | None = None, limit: int = 8,
) -> list[dict]:
    """Rank the asset pool by keyword overlap with `spec_text`, deterministically."""
    want = _terms(spec_text)
    if not want:
        return []
    active = activated or set()
    ranked: list[dict] = []
    for it in available_assets(repo_dir, asset_dir):
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
    """Find one enabled in-scope constitution by name, or None. Scope is the dirs
    and active set the caller passes."""
    want = (name or "").strip().lower()
    for it in list_constitution(mode, universal_dir, repo_dir, activated=activated, asset_dir=asset_dir):
        if it["enabled"] and it["slug"].strip().lower() == want:
            return it
    return None


# `target_scope` encodes both the universal-vs-repo axis and the mode. `core` is reserved.

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
    """The plugin root holding skills/ + agents/ for this scope. Repo scope bootstraps its
    manifest on write."""
    CONST, LOCAL, DEV_PLUGIN = _homes()
    if target_scope == "universal_dev":
        return DEV_PLUGIN
    if target_scope == "repo_dev":
        if not repo_id:
            raise ValueError("repo_dev skill/agent needs a repo_id")
        return LOCAL / repo_id / "dev"
    raise ReservedScope(f"skill/agent apply for scope '{target_scope}' is reserved")


def ensure_plugin_manifest(root: Path, name: str) -> None:
    """Bootstrap a minimal plugin manifest so a per-repo cell's skills and
    agents load. No-op if one exists."""
    manifest = Path(root) / ".claude-plugin" / "plugin.json"
    if manifest.is_file():
        return
    manifest.parent.mkdir(parents=True, exist_ok=True)
    import json
    manifest.write_text(json.dumps(
        {"name": name, "description": f"Per-repo operational plugin ({name})", "version": "0.0.1"},
        indent=2) + "\n", encoding="utf-8")


def publish_artifact(output_form: str, target_scope: str, repo_id: str | None, *,
                     slug: str, content: str, source: str = "agent",
                     created: str = "") -> str:
    """Write an approved artifact to its live home and return the path."""
    slug = slugify(slug)
    if output_form == "constitution":
        home = constitution_home(target_scope, repo_id)
        home.mkdir(parents=True, exist_ok=True)
        # `enabled` frontmatter lets runtime on/off be a flag flip, not a delete.
        content = with_frontmatter_default(content, "name", slug)
        content = with_frontmatter_default(content, "enabled", "true")
        if source:
            content = with_frontmatter_default(content, "source", source)
        if created:
            content = with_frontmatter_default(content, "created", created)
            content = with_frontmatter_default(content, "updated", created)
        path = home / f"{slug}.md"
        path.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
        return str(path)
    if output_form in ("skill", "agent"):
        root = plugin_root(target_scope, repo_id)
        if target_scope == "repo_dev":
            ensure_plugin_manifest(root, f"{repo_id}-dev")
        # `category: learned` separates loop output (visible) from shipped machinery (hidden).
        content = with_frontmatter_default(content, "category", "learned")
        if output_form == "skill":
            path = root / "skills" / slug / "SKILL.md"
        else:
            path = root / "agents" / f"{slug}.md"
            # A background runner reads this field. Injected only if forge did not set one.
            content = with_frontmatter_default(content, "effort", "medium")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
        return str(path)
    raise ValueError(f"unknown output_form: {output_form}")


# The CLI's plugin scanner ignores frontmatter, so disabling a skill or agent means moving it
# here.

_DISABLED = ".disabled"


def _plugin_artifact_paths(root: Path, kind: str, slug: str) -> tuple[Path, Path]:
    """(live, disabled) paths for a skill or agent. A skill is a directory,
    an agent a single file."""
    if kind == "skill":
        return root / "skills" / slug / "SKILL.md", root / _DISABLED / "skills" / slug / "SKILL.md"
    return root / "agents" / f"{slug}.md", root / _DISABLED / "agents" / f"{slug}.md"


def _published_paths(form: str, scope: str, repo_id: str | None, slug: str) -> dict:
    """Resolve a published artifact's on-disk home(s). Constitution → one file;
    skill/agent → the (live, disabled) pair."""
    slug = slugify(slug)
    if form == "constitution":
        return {"file": constitution_home(scope, repo_id) / f"{slug}.md"}
    if form in ("skill", "agent"):
        live, shadow = _plugin_artifact_paths(plugin_root(scope, repo_id), form, slug)
        return {"live": live, "disabled": shadow}
    raise ValueError(f"unknown form: {form}")


def published_state(form: str, scope: str, repo_id: str | None, slug: str) -> dict:
    """Live on-disk state: `present` + `enabled`. Constitution reads frontmatter;
    skill/agent infer from which tree holds them."""
    p = _published_paths(form, scope, repo_id, slug)
    if form == "constitution":
        f = p["file"]
        if not f.is_file():
            return {"present": False, "enabled": False}
        meta, _ = parse_frontmatter(f.read_text(encoding="utf-8"))
        return {"present": True, "enabled": _is_enabled(meta)}
    if p["live"].is_file():
        return {"present": True, "enabled": True}
    if p["disabled"].is_file():
        return {"present": True, "enabled": False}
    return {"present": False, "enabled": False}


def _flip_constitution(path: Path, enabled: bool) -> None:
    """Rewrite a constitution file's `enabled` frontmatter flag in place (insert it if absent)."""
    text = path.read_text(encoding="utf-8")
    val = "true" if enabled else "false"
    m = _FM.match(text)
    if not m:  # no frontmatter — wrap minimally so the flag has a home
        path.write_text(f"---\nenabled: {val}\n---\n{text.strip()}\n", encoding="utf-8")
        return
    fm, body = m.group(1), m.group(2)
    if re.search(r"(?m)^enabled:.*$", fm):
        fm = re.sub(r"(?m)^enabled:.*$", f"enabled: {val}", fm)
    else:
        fm = fm.rstrip("\n") + f"\nenabled: {val}"
    path.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")


def set_published_enabled(form: str, scope: str, repo_id: str | None, slug: str,
                          enabled: bool) -> dict | None:
    """Toggle a published artifact without deleting it. Constitution flips
    frontmatter; skill/agent move between the live and `.disabled/` trees."""
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
    """The editable markdown file for a published artifact, live or disabled.
    None if nothing is present."""
    p = _published_paths(form, scope, repo_id, slug)
    if form == "constitution":
        f = p["file"]
        return f if f.is_file() else None
    for art in (p["live"], p["disabled"]):
        if art.is_file():
            return art
    return None


def delete_published(form: str, scope: str, repo_id: str | None, slug: str) -> bool:
    """Remove a published artifact from disk, live copy and `.disabled/` shadow.
    The proposal row stays as history."""
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
