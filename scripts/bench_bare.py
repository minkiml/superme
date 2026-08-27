"""The BARE arm: one Claude Code session, no SuperMe layer, on an isolated copy of the repo.

SuperMe is `SuperMe layer + Claude Code engine`. This is the engine alone, so the difference is
what the layer costs and what it buys. Fairness is the whole point of this file, so the things that
would quietly invalidate a comparison are handled explicitly:

- **Same model, named.** Never the SDK default: SuperMe runs Sonnet, and a default-Opus control
  would both price and reason differently. `--model` is required.
- **Same accounting.** Usage is deduped per `message_id` across every assistant message, which is
  what `LiveTokens` does on the SuperMe side. `ResultMessage.usage` is parent-only and misses
  subagents, so using it would under-report exactly the arm being graded.
- **No sight of the other arm.** The clone is stripped to the base commit: every branch, tag and
  remote ref is deleted, so `git log --all` cannot surface a commit SuperMe wrote for this task.

    PYTHONPATH=. python -m scripts.bench_bare --task "..." --title "..." --model sonnet --at <sha>

Results append to `scripts/bench-result/runs.jsonl`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient

RESULTS_DIR = Path(__file__).resolve().parent / "bench-result"
PLAYGROUND = Path("/Users/cooma/Developer/my_docs/test-playground")

# USD per 1M: input · cache write · cache read · output. List price, so both arms price alike.
PRICES = {
    "sonnet": (3.0, 3.75, 0.30, 15.0),
    "opus":   (5.0, 6.25, 0.50, 25.0),
    "haiku":  (1.0, 1.25, 0.10, 5.0),
}


def price_for(model: str) -> tuple[float, float, float, float]:
    for key, p in PRICES.items():
        if key in (model or "").lower():
            return p
    raise SystemExit(f"no price table for model {model!r} — add it rather than guessing")


def _sh(cmd: list[str], cwd: Path) -> str:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def isolated_clone(src: Path, base: str) -> Path:
    """A clone at `base` with every other ref destroyed.

    A plain clone carries SuperMe's item branches, and one `git log --all` would hand the agent the
    very commit it is being asked to reproduce."""
    dst = Path(tempfile.mkdtemp(prefix="bench-")) / src.name
    subprocess.run(["git", "clone", "--quiet", "--no-tags", str(src), str(dst)],
                   check=True, capture_output=True)
    subprocess.run(["git", "checkout", "--quiet", "-B", "bench", base], cwd=str(dst),
                   check=True, capture_output=True)
    for ref in _sh(["git", "for-each-ref", "--format=%(refname)"], dst).splitlines():
        if ref.strip() and not ref.strip().endswith("/bench"):
            subprocess.run(["git", "update-ref", "-d", ref.strip()], cwd=str(dst),
                           capture_output=True)
    subprocess.run(["git", "remote", "remove", "origin"], cwd=str(dst), capture_output=True)
    subprocess.run(["git", "reflog", "expire", "--expire=now", "--all"], cwd=str(dst),
                   capture_output=True)
    subprocess.run(["git", "gc", "--prune=now", "--quiet"], cwd=str(dst), capture_output=True)
    return dst


def leak_check(repo: Path, base: str) -> dict:
    """Prove the isolation held rather than trusting the steps that set it up."""
    return {"refs": _sh(["git", "for-each-ref", "--format=%(refname)"], repo).splitlines(),
            "commits_visible": len(_sh(["git", "log", "--all", "--oneline"], repo).splitlines()),
            "head_is_base": _sh(["git", "rev-parse", "HEAD"], repo).startswith(base[:8])}


def brief(title: str, text: str, kind: str = "implementation") -> str:
    """What a person would actually type, carrying exactly what the SuperMe item carried.

    The closing instruction is the only thing that differs by kind, and it has to: telling a sweep
    to "implement it" would send it changing code nobody asked it to change."""
    head = f"The task is: {title}.\n\n" if title else ""
    close = ("Investigate and report what you find. Do not change any code."
             if kind == "research" else "Implement it in this repository.")
    return f"{head}Here is the detail, in the owner's words:\n\n{text}\n\n{close}"


async def run(task: str, repo: Path, model: str) -> dict:
    opts = ClaudeAgentOptions(
        cwd=str(repo),
        model=model,
        # The same preset SuperMe builds on, with NOTHING appended.
        system_prompt={"type": "preset", "preset": "claude_code"},
        # No SuperMe plugins, no MCP servers, no charter, no phase skill — and no personal settings
        # or skills either, so the engine is the only thing left.
        setting_sources=[],
        permission_mode="bypassPermissions",
    )
    by_msg: dict[str, dict] = {}          # message_id → usage, matching LiveTokens
    turns = 0
    tools: list[str] = []
    models_seen: set[str] = set()
    texts: list[str] = []                 # a research arm's deliverable is prose, not a diff
    started = time.time()
    async with ClaudeSDKClient(options=opts) as client:
        await client.query(task)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                turns += 1
                if msg.model:
                    models_seen.add(msg.model)
                tools += [getattr(b, "name", "?") for b in msg.content
                          if type(b).__name__ == "ToolUseBlock"]
                texts += [b.text for b in msg.content if type(b).__name__ == "TextBlock"]
                if msg.message_id:
                    by_msg[msg.message_id] = dict(msg.usage or {})
    secs = int(time.time() - started)

    tot = {k: sum(u.get(k, 0) or 0 for u in by_msg.values()) for k in
           ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
            "output_tokens")}
    pi, pw, pr, po = price_for(model)
    usd = (tot["input_tokens"] * pi + tot["cache_creation_input_tokens"] * pw
           + tot["cache_read_input_tokens"] * pr + tot["output_tokens"] * po) / 1e6
    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                           cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
    return {
        "arm": "bare", "model_asked": model, "models_used": sorted(models_seen),
        "turns": turns, "tool_calls": len(tools), "tools": tools, "seconds": secs,
        "crit_tokens": (tot["input_tokens"] + tot["cache_creation_input_tokens"]
                        + tot["output_tokens"]),
        "cache_read": tot["cache_read_input_tokens"], "usd": round(usd, 3),
        "tests_pass": tests.returncode == 0,
        "diffstat": (_sh(["git", "diff", "--stat", "HEAD"], repo).splitlines() or [""])[-1],
        "files_changed": _sh(["git", "diff", "--name-only", "HEAD"], repo).splitlines(),
        "diff": _sh(["git", "diff", "HEAD"], repo),
        # The last substantial block is what a person would read as the answer.
        "final_text": next((x for x in reversed(texts) if len(x.strip()) > 200), texts[-1] if texts else ""),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, help="the owner's words, verbatim")
    ap.add_argument("--title", default="", help="the one-line ask")
    ap.add_argument("--model", required=True, help="must match the SuperMe arm exactly")
    ap.add_argument("--at", required=True, help="base commit both arms start from")
    ap.add_argument("--kind", default="implementation",
                    choices=("implementation", "research"))
    ap.add_argument("--label", default="")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    repo = isolated_clone(PLAYGROUND, a.at)
    leak = leak_check(repo, a.at)
    print(f"clone {repo}\nbase  {a.at[:12]} · refs {leak['refs']} · "
          f"commits visible {leak['commits_visible']}\n")
    try:
        rec = asyncio.run(run(brief(a.title, a.task, a.kind), repo, a.model))
    finally:
        if not a.keep:
            shutil.rmtree(repo.parent, ignore_errors=True)
    rec |= {"kind": a.kind, "label": a.label or a.title, "base": a.at, "isolation": leak,
            "clone": str(repo) if a.keep else None}
    RESULTS_DIR.mkdir(exist_ok=True)
    with (RESULTS_DIR / "runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in rec.items() if k not in ("diff", "tools", "final_text")},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
