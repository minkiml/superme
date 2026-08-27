"""One task, two arms: the Claude Code engine alone, and SuperMe (layer + that same engine).

Order is the isolation guarantee. The BARE arm runs FIRST, from a ref-stripped clone of the base
commit, so nothing SuperMe writes for this task can exist yet, let alone be found. SuperMe then
runs the same words on the same base. Neither arm can read the other.

    PYTHONPATH=. python -m scripts.bench --title "..." --task "..." [--model sonnet] [--bare-only]

Writes `scripts/bench-result/<slug>.md` and appends both arms to `runs.jsonl`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from scripts.bench_bare import PLAYGROUND, RESULTS_DIR, price_for

CONTEXT = "test-playground"
POLL_SECONDS = 15
STALL_AFTER = 300


# Words that carry no meaning in a filename. A slugged sentence is not a name.
_NOISE = {"a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "cannot", "do", "does",
          "for", "from", "has", "have", "in", "into", "is", "it", "its", "no", "not", "of", "on",
          "once", "only", "or", "so", "that", "the", "there", "this", "to", "up", "way", "with"}


def slug_of(title: str, words: int = 4) -> str:
    """A short name from a prose title: meaning-bearing words only, capped."""
    kept = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in _NOISE]
    return "-".join(kept[:words])[:36] or "task"


# Truncating the kind gave "rese". Name the two rather than slice them.
KIND_TAG = {"implementation": "impl", "research": "research"}


def stamp() -> str:
    """Sorts the directory chronologically, and keeps a re-run from overwriting its predecessor."""
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d-%H%M")


def say(msg: str) -> None:
    """Unbuffered: a redirected run must still show progress while it works."""
    print(msg, flush=True)


def _sh(cmd: list[str], cwd: Path) -> str:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def superme_model() -> str:
    """Whatever SuperMe will actually resolve for this repo, so the bare arm matches it."""
    from superme_agent.daemon.app_state import get_spine
    return get_spine().effective_model(CONTEXT) or "sonnet"


DAEMON = "http://127.0.0.1:8787"


def fire_superme(title: str, text: str, kind: str = "implementation") -> str:
    """Through the daemon's own API. Writing the store from this process would create the row and
    leave the running daemon with no reason to fire anything."""
    import urllib.request

    def post(path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            DAEMON + path, method="POST",
            data=json.dumps(payload).encode(), headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())

    row = post("/dev/inbox", {"context_id": CONTEXT, "title": title, "text": text,
                              "work_kind": kind, "autopilot": True})
    res = post(f"/dev/inbox/{row['id']}/push", {"context_id": CONTEXT})
    return res["work_item"]["id"]


def await_superme(item_id: str) -> list[int]:
    """Block until the item stops producing runs. Returns its run ids."""
    from superme_agent.daemon.app_state import get_spine
    s = get_spine()
    last_change = time.time()
    prev: tuple = ()
    while True:
        with s._conn() as c:
            rows = c.execute("SELECT id,status FROM run WHERE item_id=? ORDER BY id",
                             (item_id,)).fetchall()
        cur = tuple((r[0], r[1]) for r in rows)
        running = any(r[1] == "running" for r in rows)
        if cur != prev:
            prev, last_change = cur, time.time()
            say(f"   … {len(rows)} runs" + (" (running)" if running else ""))
        if not running and time.time() - last_change > STALL_AFTER:
            return [r[0] for r in rows]
        time.sleep(POLL_SECONDS)


def collect_superme(item_id: str, model: str) -> dict:
    """The SuperMe arm, tallied exactly as the bare arm is."""
    from superme_agent.daemon.app_state import get_spine
    from superme_agent.gateway import contexts
    s = get_spine()
    with s._conn() as c:
        rows = c.execute("SELECT id,phase,feature,tok_input,tok_cache_creation,tok_cache_read,"
                         "tok_output FROM run WHERE item_id=? ORDER BY id", (item_id,)).fetchall()
        calls = tools = 0
        names: list[str] = []
        for r in rows:
            for (n,) in c.execute("SELECT name FROM run_event WHERE run_id=? AND kind IN "
                                  "('tool','mcp')", (r[0],)):
                names.append(n or "?")
        calls = len(names)
    i = sum(r[3] or 0 for r in rows)
    cw = sum(r[4] or 0 for r in rows)
    cr = sum(r[5] or 0 for r in rows)
    o = sum(r[6] or 0 for r in rows)
    pi, pw, pr, po = price_for(model)
    item = None
    try:
        from superme_agent.daemon import app_state
        ctx = contexts.resolve(CONTEXT, "dev")
        item = app_state.dev.read_work_item(ctx.internal_root / "dev", item_id) or {}
    except Exception:  # noqa: BLE001
        item = {}
    merge = item.get("git_merge_commit")
    diff = _sh(["git", "show", "--stat", merge], PLAYGROUND) if merge else ""
    # Research merges nothing: its deliverable is the investigation artifact.
    findings = ""
    try:
        d = ctx.internal_root / "dev" / "work-items" / item_id / "artifacts"
        for name in ("investigation.md", "review.md"):
            if (d / name).is_file():
                findings = (d / name).read_text(encoding="utf-8")
                break
    except Exception:  # noqa: BLE001
        pass
    body = _sh(["git", "show", "--format=", merge], PLAYGROUND) if merge else ""
    secs = 0
    with s._conn() as c:
        for a_, b_ in c.execute("SELECT started_at,ended_at FROM run WHERE item_id=?", (item_id,)):
            try:
                from datetime import datetime
                secs += int((datetime.fromisoformat(b_) - datetime.fromisoformat(a_))
                            .total_seconds())
            except Exception:  # noqa: BLE001
                pass
    return {
        "seconds": secs, "diff": body,
        "arm": "superme", "item_id": item_id, "model_asked": model,
        "runs": len(rows), "phases": [f"{r[1]}/{r[2]}" for r in rows],
        "turns": None,          # not measurable from run_event — see item-cost-audit.md
        "tool_calls": calls, "tools": names,
        "crit_tokens": i + cw + o, "cache_read": cr,
        "usd": round((i * pi + cw * pw + cr * pr + o * po) / 1e6, 3),
        "merge_commit": merge, "final_text": findings,
        "diffstat": (diff.splitlines() or [""])[-1].strip(),
    }


def _phase_rows(item_id: str, model: str) -> list[str]:
    """Where SuperMe's spend went. The layer's cost is per-run, so the per-run split is the story."""
    from superme_agent.daemon.app_state import get_spine
    pi, pw, pr, po = price_for(model)
    out = ["| run | phase | tool calls | crit tok | USD |", "|---|---|---:|---:|---:|"]
    with get_spine()._conn() as c:
        for rid, ph, ft, i, cw, cr, o in c.execute(
                "SELECT id,phase,feature,tok_input,tok_cache_creation,tok_cache_read,tok_output "
                "FROM run WHERE item_id=? ORDER BY id", (item_id,)):
            i, cw, cr, o = (x or 0 for x in (i, cw, cr, o))
            n = c.execute("SELECT COUNT(*) FROM run_event WHERE run_id=? AND kind IN "
                          "('tool','mcp')", (rid,)).fetchone()[0]
            usd = (i * pi + cw * pw + cr * pr + o * po) / 1e6
            out.append(f"| {rid} | {ph}/{ft} | {n} | {i + cw + o:,} | ${usd:.2f} |")
    return out


def report(title: str, task: str, base: str, bare: dict, sm: dict | None,
           kind: str = "implementation") -> str:
    iso = bare["isolation"]
    L = [f"# Bench — {title}", "",
         f"> {task.strip()}", "",
         f"`{base[:12]}` · model `{bare['model_asked']}` on both arms · "
         f"bare arm actually used {', '.join(bare['models_used']) or '?'}.", ""]

    if sm:
        ratio = sm["usd"] / bare["usd"] if bare["usd"] else 0
        L += [f"**{ratio:.0f}x the cost for {'the same' if bare['diffstat'] == sm['diffstat'] else 'a different'} "
              f"change.** Read the quality section before drawing anything from that.", ""]

    L += ["## Cost", "", "| | bare engine | SuperMe | ratio |", "|---|---:|---:|---:|"]
    if sm:
        def row(label, b, s, fmt="{:,}"):
            L.append(f"| {label} | {fmt.format(b)} | {fmt.format(s)} | "
                     f"{f'{s / b:.0f}x' if b else '—'} |")
        row("critical tokens", bare["crit_tokens"], sm["crit_tokens"])
        row("cache read", bare["cache_read"], sm["cache_read"])
        row("tool calls", bare["tool_calls"], sm["tool_calls"])
        row("wall clock (s)", bare["seconds"], sm["seconds"])
        row("USD", bare["usd"], sm["usd"], "${:.2f}")
        L += ["", f"The bare arm is one session of {bare['turns']} assistant turns. SuperMe is "
                  f"{sm['runs']} separate runs.", "",
              "### Where SuperMe's went", ""] + _phase_rows(sm["item_id"], bare["model_asked"])

    L += ["", "## What each produced", ""]
    if kind == "research":
        # A sweep that edited code did the wrong job, so say whether it stayed read-only.
        L += [f"- **bare** — {len(bare['final_text'])} chars of findings · "
              f"touched {len(bare['files_changed'])} file(s) "
              f"({'read-only, correct' if not bare['files_changed'] else 'SHOULD HAVE BEEN NONE'})"]
        if sm:
            L.append(f"- **SuperMe** — {len(sm.get('final_text') or '')} chars in its "
                     f"investigation artifact")
        L += ["", "### The bare arm's findings", "", bare["final_text"][:6000] or "(none)", ""]
        if sm and sm.get("final_text"):
            L += ["### SuperMe's findings", "", sm["final_text"][:6000], ""]
    else:
        L += [f"- **bare** — {bare['diffstat']} · tests "
              f"{'pass' if bare['tests_pass'] else '**FAIL**'} · "
              f"{', '.join(bare['files_changed']) or '(nothing)'}"]
        if sm:
            L.append(f"- **SuperMe** — {sm['diffstat']} · merged "
                     f"`{(sm['merge_commit'] or '')[:12]}`")
        L += ["", "### The bare arm's diff", "", "```diff",
              bare["diff"][:4000] or "(empty)", "```", ""]
        if sm and sm.get("diff"):
            L += ["### SuperMe's diff", "", "```diff", sm["diff"][:4000], "```", ""]

    L += ["## Quality", "",
          ("_Judged by hand. For a sweep: is each finding REAL — does the symbol truly have no "
           "caller — and does it carry proof, or just an assertion? A confident false finding is "
           "worse than no finding._" if kind == "research" else
           "_Judged by hand — a diffstat cannot see a wrong answer. Does each arm's code match the "
           "conventions already in the repo, cover the same cases, and answer what was asked?_"),
          "",
          "## What this establishes, and what it does not", "",
          f"- One task, one repo, one model, n=1. A single run cannot separate the layer's cost "
          f"from run-to-run variance.",
          f"- Isolation is proven, not assumed: {iso['commits_visible']} commits reachable, refs "
          f"`{iso['refs']}`, HEAD at base `{iso['head_is_base']}`. The bare arm ran BEFORE the "
          f"SuperMe item existed.",
          "- Both arms are tallied the same way: usage deduped per `message_id`, list price for "
          "the named model. Neither figure is an estimate.",
          "- Cost ratios say nothing about whether the extra spend bought anything. That is the "
          "quality section's job, and it is written by a person.", ""]

    L += ["## The bare arm's tool calls", "", f"`{'` · `'.join(bare['tools']) or '(none)'}`", ""]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--model", default=None, help="defaults to whatever SuperMe would resolve")
    ap.add_argument("--kind", default="implementation",
                    choices=("implementation", "research"))
    ap.add_argument("--slug", default="", help="short filename stem; derived if omitted")
    ap.add_argument("--bare-only", action="store_true")
    a = ap.parse_args()

    model = a.model or superme_model()
    base = _sh(["git", "rev-parse", "HEAD"], PLAYGROUND)
    RESULTS_DIR.mkdir(exist_ok=True)
    say(f"base {base[:12]} · model {model}\n")

    say("[1/2] bare engine (first, so it cannot see the other arm)")
    r = subprocess.run([sys.executable, "-m", "scripts.bench_bare", "--task", a.task,
                        "--title", a.title, "--model", model, "--at", base,
                        "--kind", a.kind, "--label", a.title], capture_output=True, text=True, encoding="utf-8")
    print(r.stdout[-1200:] or r.stderr[-1200:])
    bare = json.loads((RESULTS_DIR / "runs.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    if bare.get("arm") != "bare":
        sys.exit("bare arm did not record a result")

    sm = None
    if not a.bare_only:
        say("\n[2/2] SuperMe (layer + the same engine)")
        item = fire_superme(a.title, a.task, a.kind)
        say(f"   work-item {item}")
        await_superme(item)
        sm = collect_superme(item, model)
        with (RESULTS_DIR / "runs.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(sm, ensure_ascii=False) + "\n")

    out = RESULTS_DIR / f"{stamp()}-{KIND_TAG[a.kind]}-{a.slug or slug_of(a.title)}.md"
    out.write_text(report(a.title, a.task, base, bare, sm, a.kind), encoding="utf-8")
    say(f"\nreport → {out}")


if __name__ == "__main__":
    main()
