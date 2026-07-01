#!/usr/bin/env python3
"""forge_kit/eval.py — the behavioural check for a forged artifact.

Structural lint proves the artifact is *well-formed*; this proves it is *good* — by putting it in
front of a fresh model and seeing how it behaves. The methodology is lifted from our own
best-practice guide's Validation section:

  * skill  — Discovery (does the description route the right requests to it, and keep the wrong
             ones out?) + Logic (simulate an agent executing the steps on a realistic task; where
             is it forced to guess?).
  * agent  — Delegation (would the main agent hand this worker the right job?) + Self-containment
             (an isolated worker sees only its own brief — is that brief enough to act?).
  * constitution — Clarity (one unambiguous, actionable directive?) + Conflict (does it contradict
             or duplicate the rules already in force in this scope?).

It runs a single hermetic `claude -p` one-shot and returns a JSON report. Eval is *evidence for the
gate-2 reviewer*, not a hard gate: a `fail` verdict means the author should revise and re-run, but
the report is always emitted (exit 0). If the model call itself can't run, the report says so
(`verdict: "skipped"`) rather than blocking the pipeline.

Usage:
    python eval.py <form> <file> --intent "<what this artifact is for>" \
        [--name <slug>] [--scope <target_scope>] [--existing <path>] [--model <id>]

The full JSON report is printed to stdout (last line is the compact JSON for machine capture).
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
# The trial run that measures the ARTIFACT's own cost is sandboxed hard: read-only tools (so an
# unvetted learned artifact can never mutate the machine) and a turn cap (so it can't run away on
# cost). Cheaper budget than the review since we only want a ballpark.
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
        "ambiguous, missing, or force you to guess."
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
        "You are auditing a constitution item — a single always-on directive injected into an "
        "agent's system prompt every turn. Two lenses:\n"
        "1) CLARITY — is it ONE unambiguous, actionable directive (not vague, not an essay, not "
        "several rules bundled together)?\n"
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
    """claude -p records a native session transcript under ~/.claude/projects/<slugified-cwd>/.
    Our cwd is a throwaway tempdir, so that transcript is pure orphaned cruft once the eval is done —
    delete it. Keyed on the tempdir's unique basename (e.g. 'tmpbhabgded'), which can't collide with
    a real project dir, so the glob is safe."""
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
    """Reduce the envelope to the HONEST resource figures, not the misleading cumulative one.

    A naive 'total tokens' sums input+output+cache across every turn — but in an N-turn agentic loop
    each turn re-reads the same growing context from cache, so that total is ~`turns × context` and
    is 99% cheap cache re-reads. It is neither the context the worker held nor a coherent
    'consumption'. So we report what's actually meaningful:
      • context — the working set the worker held PER TURN ≈ cumulative input ÷ turns (what 'the
        context showed'; ~tens of k, matching the run's context_pct).
      • output  — net NEW text generated across the run (small; the real product of the work).
      • turns   — how many agentic steps it took.
    Cost (total_cost_usd) and time remain the truest billed measures. `modelUsage` is the cumulative
    per-model source (its costUSD reconciles to total_cost_usd); fall back to top-level usage."""
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
    """One hermetic claude -p call (JSON output, so we also get real run metrics — tokens, time,
    cost). Hermetic = a throwaway cwd so the operated repo's settings/skills/memory don't leak into
    the judgment, AND its native transcript is purged after. `extra_args` lets the trial run pin
    read-only tools and a turn cap. Returns (reply_text, metrics)."""
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
    # Parse the JSON envelope regardless of exit code: `claude -p` exits NON-ZERO on a soft outcome
    # like `error_max_turns` (the trial's turn cap is the expected way an exercise ends) yet still
    # emits a complete result envelope with real usage/cost/duration. Discarding that would throw
    # away a genuine — if turn-capped — run. Only a missing/unparseable envelope is a hard failure.
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
    """Ballpark the ARTIFACT's OWN run cost (not the review's) on one synthetic task.

    A constitution never "runs" — it is always-on text injected into the system prompt every turn,
    so its real resource cost is the per-turn token overhead it adds; we estimate that from length
    (~4 chars/token) and do no model call. A skill/agent does run, so we exercise its body once on a
    synthetic task, read-only and turn-bounded — safe (can't mutate the machine), cheap, and still
    reflective of real work. Returns a metrics dict tagged with `kind` so the UI can render it."""
    if form == "constitution":
        toks = max(1, (len(artifact.strip()) + 3) // 4)
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

    # schema_version stamps the report shape (R5): 1 = {verdict, summary, checks[], issues[],
    # metrics{}, trial_task}. Lets readers tell post-versioning reports from pre-metrics legacy rows.
    report = {"schema_version": 1, "form": args.form, **report}

    # Trial run — the headline metric: the artifact's OWN run cost on a synthetic task. Skip only if
    # the review itself couldn't run (the model is unreachable, so a trial would fail too).
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
