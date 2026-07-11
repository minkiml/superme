#!/usr/bin/env python3
"""forge_kit/lint.py — the deterministic structural check for a forged artifact.

The shape of an operational artifact (constitution / skill / agent) is strict and knowable
without a model: frontmatter parses, the name is kebab and matches its published home, the
description fits the routing budget, the body stays lean. That is exactly the work to hand a
script rather than ask the author to "be careful" — so the `forge` agent runs this, reads the
findings, and self-corrects before it stages.

Usage:
    python lint.py <form> <file> [--name <expected-slug>]

    <form>   constitution | skill | agent
    <file>   path to the authored artifact on disk
    --name   the slug the artifact will publish under (skill/agent dir name). When given, the
             frontmatter `name` must match it — a mismatch breaks discovery on disk.

Exit code 0 = clean (artifact may stage). Non-zero = blocking errors found; stdout lists them
plus any non-blocking warnings, each on its own line, so the agent can fix and re-run.
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # the harness env ships pyyaml; degrade loudly rather than silently pass
    yaml = None

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_DESC = 1024          # routing budget — the only metadata an agent sees when choosing
MAX_NAME = 64
# A LEARNED artifact must use a model ALIAS, never a pinned full ID. Aliases are the canonical on-disk
# form everywhere in SuperMe; the backend resolves them to the latest concrete id at consumption
# (core/models.py MODEL_TIERS → resolve_agent_model / normalize_model). A pinned id would go stale (or,
# like a hallucinated `claude-sonnet-4-5`, be invalid) and needs a file rewrite on every model bump. So
# lint allows only these.
MODEL_ALIASES = {"sonnet", "opus", "haiku", "inherit"}
SKILL_BODY_HARD = 500    # spec ceiling for SKILL.md
LEAN_BODY_WARN = 200     # a *learned* skill/agent should be tighter than the ceiling


def _split_frontmatter(text):
    """Return (frontmatter_dict_or_None, body_str). None frontmatter ⇒ no leading --- block."""
    if not text.startswith("---"):
        return None, text
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not m:
        return False, text  # opened a fence it never closed — caller treats False as "malformed"
    fm_text, body = m.group(1), m.group(2)
    if yaml is None:
        return False, body
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        return False, body
    return (data if isinstance(data, dict) else False), body


def _check_meta(fm, body, *, expected_name, want_tools, errors, warnings):
    """Shared frontmatter checks for skill + agent (both carry name/description frontmatter)."""
    if fm is None:
        errors.append("No YAML frontmatter — the file must open with a '---' fenced block.")
        return
    if fm is False:
        errors.append("Frontmatter is malformed (unclosed fence or invalid YAML).")
        return

    name = str(fm.get("name", "")).strip()
    if not name:
        errors.append("Frontmatter is missing `name`.")
    else:
        if len(name) > MAX_NAME:
            errors.append(f"`name` is {len(name)} chars (max {MAX_NAME}).")
        if not NAME_RE.match(name):
            errors.append(f"`name` '{name}' must be kebab-case (lowercase, digits, single hyphens).")
        if expected_name and name != expected_name:
            errors.append(f"`name` '{name}' must match the publish slug '{expected_name}' "
                          f"(it becomes the on-disk folder/file name).")

    desc = str(fm.get("description", "")).strip()
    if not desc:
        errors.append("Frontmatter is missing `description` — it is the only thing an agent sees "
                      "when deciding to load this; it cannot be blank.")
    else:
        if len(desc) > MAX_DESC:
            errors.append(f"`description` is {len(desc)} chars (max {MAX_DESC}).")
        if "<" in desc or ">" in desc:
            errors.append("`description` must not contain angle brackets (< or >).")
        if "use when" not in desc.lower():
            warnings.append("`description` has no 'Use when …' trigger clause — discovery suffers "
                            "without explicit triggers.")

    if want_tools and not str(fm.get("tools", "")).strip():
        warnings.append("No `tools` allowlist — an agent should be handed only the tools its role "
                        "needs, not the whole pool.")

    model = str(fm.get("model", "")).strip()
    if model and model not in MODEL_ALIASES:
        errors.append(f"`model` '{model}' must be an alias ({', '.join(sorted(MODEL_ALIASES))}), not "
                      "a pinned ID — pinned model IDs go stale or may be invalid; aliases always "
                      "resolve to the current model.")

    if not body.strip():
        errors.append("The body is empty — there is nothing for the artifact to instruct.")
    else:
        n = len(body.strip().splitlines())
        if n > SKILL_BODY_HARD:
            errors.append(f"Body is {n} lines (ceiling {SKILL_BODY_HARD}); split detail into "
                          "references/ and point at it just-in-time.")
        elif n > LEAN_BODY_WARN:
            warnings.append(f"Body is {n} lines — a learned artifact should be leaner; keep only "
                            "what changes behaviour.")


def lint_constitution(text, *, expected_name, errors, warnings):
    """A constitution is frontmatter-first: a required `description` (the always-resident catalog
    line — a rule's directive, or a reference's what + when-to-pull) plus an OPTIONAL body (a rule's
    why/example, or a reference's substance). `name` and the runtime/provenance fields
    (enabled/scope/source/created/category) are stamped at publish, so the authored artifact need
    only carry `description` (+ optional body)."""
    fm, body = _split_frontmatter(text)
    if fm is None:
        errors.append("A constitution must open with a '---' frontmatter block carrying a "
                      "`description` (the always-on catalog line).")
        return
    if fm is False:
        errors.append("Frontmatter is malformed (unclosed fence or invalid YAML).")
        return
    name = str(fm.get("name", "")).strip()   # optional — publish injects it from the slug
    if name:
        if not NAME_RE.match(name):
            errors.append(f"`name` '{name}' must be kebab-case (lowercase, digits, single hyphens).")
        if expected_name and name != expected_name:
            errors.append(f"`name` '{name}' must match the publish slug '{expected_name}'.")
    desc = str(fm.get("description", "")).strip()
    if not desc:
        errors.append("Frontmatter is missing `description` — the always-resident catalog line "
                      "(a rule's directive, or a reference's what + when-to-pull); it cannot be blank.")
    elif len(desc) > MAX_DESC:
        errors.append(f"`description` is {len(desc)} chars (max {MAX_DESC}).")
    elif "<" in desc or ">" in desc:
        errors.append("`description` must not contain angle brackets (< or >) — make it concrete.")
    b = body.strip()
    if b:
        if b.lower() == desc.lower():
            warnings.append("The body just restates the `description` — omit it (the description is "
                            "already always-resident), or make the body real elaboration / substance.")
        elif len(b.splitlines()) > SKILL_BODY_HARD:
            errors.append(f"Body is {len(b.splitlines())} lines (ceiling {SKILL_BODY_HARD}) — trim.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("form", choices=["constitution", "skill", "agent"])
    ap.add_argument("file")
    ap.add_argument("--name", default="", help="expected publish slug (skill/agent name match)")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(f"FAIL: file not found: {path}")
        sys.exit(2)
    text = path.read_text()

    errors, warnings = [], []
    if args.form == "constitution":
        lint_constitution(text, expected_name=args.name, errors=errors, warnings=warnings)
    else:
        fm, body = _split_frontmatter(text)
        _check_meta(fm, body, expected_name=args.name,
                    want_tools=(args.form == "agent"), errors=errors, warnings=warnings)

    for w in warnings:
        print(f"WARN: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    if errors:
        print(f"\nFAIL: {len(errors)} blocking issue(s). Fix and re-run lint before staging.")
        sys.exit(1)
    print(f"PASS: structural lint clean"
          + (f" ({len(warnings)} warning(s) to consider)" if warnings else "") + ".")
    sys.exit(0)


if __name__ == "__main__":
    main()
