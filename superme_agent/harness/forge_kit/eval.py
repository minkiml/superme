#!/usr/bin/env python3
"""The behavioural check for a forged artifact.

Structural lint proves it is well-formed; this proves it is good, by putting it in front of a fresh
model. The report is always emitted.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT_S = 180
# Read-only tools and a turn cap, so an unvetted artifact can neither mutate the machine nor run
# away on cost.
_TRIAL_TOOLS = "Read,Grep,Glob"
_TRIAL_MAX_TURNS = 8
_TRIAL_TIMEOUT_S = 150

_RUBRIC = {
    "skill": (
        "You are auditing an Agent Skill (a SKILL.md) that another agent authored. Two lenses:\n"
        "1) DISCOVERY — from the frontmatter `description` ALONE, write 3 realistic requests that "
        "should load this skill and 3 near-miss requests that share words but should NOT. Judge "
        "whether the description routes them correctly; call out over-broad or too-narrow wording.\n"
        "2) LOGIC — act as an agent that just loaded this skill to do a realistic task in its "
        "domain. Walk the body step by step and flag every point where the instructions are "
        "ambiguous, missing, or force you to guess -- see, `./references/principle-for-skills.md`,the best practices for skills ."
    ),
    "agent": (
        "You are auditing a sub-agent definition (an agent.md) that another agent authored. Two "
        "lenses:\n"
        "1) DELEGATION — from the frontmatter `description` ALONE, judge whether a main agent would "
        "correctly hand this worker the right jobs (and not the wrong ones).\n"
        "2) SELF-CONTAINMENT — a sub-agent runs in an isolated context and sees ONLY its own brief. "
        "Read the body as that worker and flag anything it would need but isn't told — unstated "
        "inputs, missing return contract, reliance on conversation it can't see."
    ),
    "constitution": (
        "You are auditing a constitution item — frontmatter-first: a `description` (the always-"
        "resident catalog line the agent sees) plus an optional body it pulls on demand. Two "
        "lenses:\n"
        "1) CLARITY — is the `description` ONE coherent, self-sufficient line? For a rule, an "
        "unambiguous, actionable directive; for a reference/contract, a clear statement of what it "
        "covers and when to pull it. Not vague, not several rules bundled with 'and'. A substantive "
        "body is fine for a reference — judge the description, and that the body matches it.\n"
        "2) CONFLICT — compare it against the existing in-force rules provided below; does it "
        "contradict, duplicate, or undercut any of them?"
    ),
}

# The two scored lenses per form (stable names → the report/UI can show "discovery 4/5 · logic 3/5").
_LENSES = {
    "skill": ["discovery", "logic"],
    "agent": ["delegation", "self_containment"],
    "constitution": ["clarity", "conflict"],
}


def _schema(form):
    lenses = " and ".join(f'"{n}"' for n in _LENSES[form])
    return (
        '{"verdict":"pass|warn|fail",'
        '"summary":"2-4 terse bullets, each <=12 words, concrete and on-point — the at-a-glance read; '
        'NOT prose, NO preamble. Return as a JSON array of strings.",'
        f'"checks":[{{"name":<{lenses}>,"score":<0-5, 5=excellent>,"note":"<=12 words"}}],'
        '"issues":[{"severity":"high|low","what":"the defect in <=20 words","fix":"the fix in <=20 words"}],'
        '"trial_task":"one concrete, realistic task (<=20 words) that would exercise this artifact"}'
    )


def _build_prompt(form, artifact, intent, existing):
    parts = [
        _RUBRIC[form],
        "",
        f"WHAT THIS ARTIFACT IS FOR (author's intent):\n{intent or '(not provided)'}",
        "",
        "THE ARTIFACT UNDER REVIEW:",
        "```",
        artifact.strip(),
        "```",
    ]
    if form == "constitution" and existing:
        parts += ["", "RULES ALREADY IN FORCE IN THIS SCOPE:", "```", existing.strip(), "```"]
    parts += [
        "",
        "Be a tough but fair reviewer, and TERSE — the reader approves with no backstory, so give "
        "scannable signal, not prose. Score each lens 0-5. `fail` = a real defect that would mislead "
        "or misfire; `warn` = sound but improvable; `pass` = ready. List only genuine issues "
        "(high = would misfire in practice, low = improvable); if there are none, return an empty "
        "list. No strengths, no padding.",
        "",
        "Respond with ONLY a JSON object, no prose around it, in exactly this shape:",
        _schema(form),
    ]
    return "\n".join(parts)


def _purge_native_transcript(cwd_path):
    """`claude -p` records a native transcript under the user's projects dir. Our cwd is a throwaway
    tempdir, so that transcript is orphaned cruft — delete it."""
    base = Path(cwd_path).name
    projroot = Path.home() / ".claude" / "projects"
    if base:
        for d in projroot.glob(f"*{base}*"):
            shutil.rmtree(d, ignore_errors=True)


def _strip_frontmatter(text):
    """Return the body below a leading `---` frontmatter fence (or the whole text if there is none)."""
    if text.startswith("---"):
        m = re.match(r"^---\n.*?\n---\n?(.*)$", text, re.DOTALL)
        if m:
            return m.group(1).strip()
    return text.strip()


def _run_footprint(env_obj):
    """Reduce the envelope to the HONEST resource figures, not the cumulative one.

    A naive total re-counts the same growing context every turn. Reported instead: `context` per turn,
    net new `output`, and `turns`."""
    turns = max(int(env_obj.get("num_turns") or 1), 1)
    mu = env_obj.get("modelUsage")
    if isinstance(mu, dict) and mu:
        rows = [d for d in mu.values() if isinstance(d, dict)]
        output = sum(int(d.get("outputTokens", 0) or 0) for d in rows)
        input_cum = sum(int(d.get(k, 0) or 0) for d in rows
                        for k in ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"))
    else:
        u = env_obj.get("usage") or {}
        output = int(u.get("output_tokens", 0) or 0)
        input_cum = (int(u.get("input_tokens", 0) or 0)
                     + int(u.get("cache_read_input_tokens", 0) or 0)
                     + int(u.get("cache_creation_input_tokens", 0) or 0))
    return {"context_tokens": round(input_cum / turns), "output_tokens": output, "turns": turns}


def _run_claude(prompt, model, *, extra_args=None, timeout=TIMEOUT_S):
    """One hermetic `claude -p` call, JSON output, so real run metrics come back with the reply.

    Hermetic means a throwaway cwd, so the operated repo's settings and memory cannot leak into the
    judgment, and its native transcript is purged after."""
    cmd = ["claude", "-p", "--strict-mcp-config", "--output-format", "json"]
    if extra_args:
        cmd += extra_args
    if model:
        cmd += ["--model", model]
    env = dict(os.environ, CLAUDE_CODE_DISABLE_AUTO_MEMORY="1")
    with tempfile.TemporaryDirectory() as td:
        try:
            out = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                timeout=timeout, cwd=td, env=env,
            )
        finally:
            _purge_native_transcript(td)
    # `claude -p` exits non-zero on a soft outcome like a turn cap, yet still emits a complete
    # envelope.
    env_obj = None
    if out.stdout:
        try:
            env_obj = json.loads(out.stdout)
        except json.JSONDecodeError:
            env_obj = None
    if not isinstance(env_obj, dict):
        raise RuntimeError((out.stderr or out.stdout or "claude -p failed").strip()[:500])
    metrics = {
        **_run_footprint(env_obj),
        "duration_s": round((env_obj.get("duration_ms") or 0) / 1000, 1),
        "cost_usd": round(env_obj.get("total_cost_usd") or 0, 4),
    }
    # Flag a soft/capped outcome so the caller can present the figure as a FLOOR, not a full run.
    subtype = env_obj.get("subtype")
    if env_obj.get("is_error") or (subtype and subtype != "success"):
        metrics["capped"] = subtype or "error"
    return env_obj.get("result") or "", metrics


def _trial_run(form, artifact, *, task, model):
    """Ballpark the ARTIFACT's own run cost on one synthetic task.

    A constitution never runs, so its overhead is estimated from its description's length. A skill or
    agent is exercised once, read-only and turn-bounded."""
    if form == "constitution":
        m = re.search(r"^description:\s*(.+)$", artifact, re.MULTILINE)
        resident = m.group(1).strip() if m else artifact.strip()
        toks = max(1, (len(resident) + 3) // 4)
        return {"kind": "overhead", "tokens_per_turn": toks}
    body = _strip_frontmatter(artifact)
    label = "skill" if form == "skill" else "worker brief"
    prompt = (
        f"You are an agent carrying out the {label} below on a real task. Execute it fully and "
        f"concretely using only the read-only tools you have; do the actual work, don't just "
        f"describe it.\n\n=== {label.upper()} ===\n{body}\n\n=== TASK ===\n"
        f"{task or 'Perform a representative task in this artifact’s domain.'}"
    )
    _, metrics = _run_claude(
        prompt, model,
        extra_args=["--allowedTools", _TRIAL_TOOLS, "--max-turns", str(_TRIAL_MAX_TURNS)],
        timeout=_TRIAL_TIMEOUT_S,
    )
    metrics["kind"] = "run"
    return metrics


def _extract_json(text):
    """Pull the first balanced {...} object out of the model's reply."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    raise ValueError("unbalanced JSON object in reply")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("form", choices=["constitution", "skill", "agent"])
    ap.add_argument("file")
    ap.add_argument("--intent", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--scope", default="")
    ap.add_argument("--existing", default="", help="path to current in-force rules (constitution)")
    ap.add_argument("--model", default="")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.is_file():
        print(json.dumps({"verdict": "skipped", "summary": f"file not found: {path}"}))
        sys.exit(0)
    artifact = path.read_text()
    existing = ""
    if args.existing and Path(args.existing).is_file():
        existing = Path(args.existing).read_text()

    prompt = _build_prompt(args.form, artifact, args.intent, existing)
    eval_overhead = {}
    try:
        reply, eval_overhead = _run_claude(prompt, args.model)
        report = _extract_json(reply)
        report.setdefault("verdict", "warn")
    except subprocess.TimeoutExpired:
        report = {"verdict": "skipped", "summary": f"eval timed out after {TIMEOUT_S}s"}
    except Exception as e:
        report = {"verdict": "skipped", "summary": f"eval could not run: {e}"}

    # `schema_version` stamps the report shape, so a reader can tell it from a pre-metrics legacy
    # row.
    report = {"schema_version": 1, "form": args.form, **report}

    # Skip only when the review itself could not run: the model is unreachable, so a trial would
    # fail too.
    metrics = {}
    if report.get("verdict") != "skipped":
        try:
            metrics = _trial_run(args.form, artifact,
                                 task=report.get("trial_task", ""), model=args.model)
        except subprocess.TimeoutExpired:
            metrics = {"kind": "run", "error": f"trial timed out after {_TRIAL_TIMEOUT_S}s"}
        except Exception as e:
            metrics = {"kind": "run", "error": str(e)[:160]}
    if metrics:
        report["metrics"] = metrics            # artifact-run cost (what the card shows)
    _ = eval_overhead  # the review's own cost is no longer surfaced

    # Human-readable echo, then the compact JSON on the final line for machine capture.
    print(f"verdict: {report.get('verdict')}")
    checks = report.get("checks") or []
    if checks:
        print("scores: " + " · ".join(f"{c.get('name')} {c.get('score')}/5" for c in checks))
    if metrics.get("kind") == "overhead":
        print(f"artifact cost: ~{metrics['tokens_per_turn']} tok/turn (always-on)")
    elif metrics.get("error"):
        print(f"artifact cost: trial did not complete — {metrics['error']}")
    elif metrics:
        cap = f"  [floor — {metrics['capped']}]" if metrics.get("capped") else ""
        print(f"artifact cost: Eval on: {report.get('trial_task', 'n/a')}")
        print(f"  context ~{metrics.get('context_tokens', 0):,} tok · "
              f"output {metrics.get('output_tokens', 0):,} tok · {metrics.get('duration_s')}s{cap}")
    _summary = report.get("summary", "")
    if isinstance(_summary, list):
        for b in _summary:
            print(f"  • {b}")
    else:
        print(f"summary: {_summary}")
    for it in report.get("issues", []) or []:
        print(f"  [{it.get('severity', '?')}] {it.get('what', '')} → fix: {it.get('fix', '')}")
    print(json.dumps(report, separators=(",", ":")))
    sys.exit(0)


if __name__ == "__main__":
    main()
