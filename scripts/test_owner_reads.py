"""Two things the owner reads: the materials line, and a report's own heading.

Measured 2026-08-29: agents composed `work-items/<id>/plan.md` and missed 23 times since Aug 1 —
the real path carries an `artifacts/` segment. One vet run listed the folder and STILL built the
wrong path, twice, so it ran without the brief. Naming the paths removes the composing.

Run: PYTHONPATH=. python scripts/test_owner_reads.py
"""

import sys
import tempfile
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superme_agent.core.kernel_speech import _materials_on_disk, work_item_preamble  # noqa: E402

PASS = 0


def ok(label: str, cond, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {label} {detail}"
    PASS += 1
    print(f"  ok - {label}")


def item(root: Path, *files: str) -> Path:
    d = root / "work-items" / "abc123abc123"
    for rel in files:
        f = d / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", encoding="utf-8")
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        d = item(root, "artifacts/brief.md", "artifacts/plan.md", "reports/report-triage.md")
        line = _materials_on_disk(d)
        ok("every file is named with its real subdirectory",
           "`artifacts/brief.md`" in line and "`artifacts/plan.md`" in line
           and "`reports/report-triage.md`" in line)
        # THE POINT. The bare form is what the agent kept composing; it must not appear.
        ok("the bare `plan.md` form is nowhere in the line", "`plan.md`" not in line)
        ok("nor the bare `brief.md`", "`brief.md`" not in line)

        d2 = item(root / "b", "artifacts/brief.md", "artifacts/build-vet-1.md",
                  "artifacts/build-vet-2.md", "artifacts/build-vet-3.md", "artifacts/plan.md")
        line2 = _materials_on_disk(d2)
        ok("a numbered cycle series collapses to one entry",
           "`artifacts/build-vet-1..3.md`" in line2)
        ok("...and collapsing never drops a DISTINCT name",
           "brief.md" in line2 and "plan.md" in line2)
        ok("collapsing is not truncation — nothing says 'more'", "more" not in line2)

        d3 = item(root / "c")
        ok("an item with nothing written says so, rather than listing a path that misleads",
           _materials_on_disk(d3) == " — nothing written yet")
        ok("a folder that does not exist yet is not an error",
           _materials_on_disk(root / "c" / "work-items" / "nope") == " — nothing written yet")
        ok("an unreadable path returns empty rather than raising",
           _materials_on_disk(None) == "")

        # It has to reach the preamble, not just exist.
        out = work_item_preamble("abc123abc123", {"phase": "vet", "kind": "implementation"},
                                 str(d), interactive=False)
        materials = next(ln for ln in out.splitlines() if ln.startswith("- materials:"))
        ok("the preamble's materials line carries it", "artifacts/plan.md" in materials)
        ok("...and still names the item folder itself", str(d) in materials)

    report_heading()


def report_heading() -> None:
    """`file_phase_report` REFUSES a malformed report instead of filing it.

    Measured across 652 filed reports: 21 opened with something other than their `# ` title (16 of
    them in August alone, so this is current) and 3 carried junk on the heading line — `ptr#`,
    `-#`, `\\#`. The owner reads both verbatim in the drilldown."""
    import asyncio, tempfile
    from superme_agent.core.artifacts import report_body_issues as issues
    from superme_agent.harness.tools.dev_tools.reports import _file_phase_report
    print("\nreport bodies — malformed ones are refused, not repaired")

    # Verbatim from filed reports, not invented.
    for junk in ("ptr", "-", "\\"):
        out = issues(f"{junk}# Investigation User-facing Report")
        ok(f"`{junk}#` on the heading line is refused", out and "before its `#`" in out[0])
        ok(f"...and the fix is quoted back for `{junk}#`", "# Investigation" in out[0])
    for lead in ("**Summary:** it worked", "**Workflow:** Implementation",
                 "<!-- Close report -->", "Triage User-facing Brief"):
        ok(f"a body opening {lead[:22]!r} is refused", bool(issues(lead)))

    ok("a proper title passes", issues("# Review User-facing Report\n\nbody") == [])
    ok("blank lines before the title are fine", issues("\n\n# Title") == [])
    ok("a deeper heading is still a heading", issues("## Sub\n") == [])

    # A greedy rule would eat real content.
    ok("a `#` mid-line is not a heading and is not 'repaired'",
       issues("# T\n\n**Summary:** issue #42 closed") == [])
    ok("an unfilled template placeholder is caught", issues("# T\n\n{summary}"))
    ok("...but braces inside inline code are not", issues("# T\n\nthe `{summary}` slot") == [])
    ok("...nor inside a fenced block", issues("# T\n\n```\n{x_y}\n```") == [])

    # The refusal must actually stop the write.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = root / "work-items" / "abc123abc123"
        (d / "reports").mkdir(parents=True)
        (d / "item.md").write_text("---\nid: abc123abc123\nphase: review\n---\n",
                                   encoding="utf-8")
        h = _file_phase_report(store=None, context_id="t", dev_root=root,
                               bound_item_id="abc123abc123", scope="review")
        r = asyncio.run(h({"item_id": "abc123abc123", "body": "ptr# Review User-facing Report"}))
        ok("the tool refuses", r.get("is_error"))
        ok("...and says nothing was written", "nothing was written" in r["content"][0]["text"])
        ok("...and NO file was left behind", not (d / "reports" / "report-review.md").exists())
        r = asyncio.run(h({"item_id": "abc123abc123",
                           "body": "# Review User-facing Report\n\nAll good.\n"}))
        ok("a well-formed report still files", not r.get("is_error"))
        ok("...and lands on disk", (d / "reports" / "report-review.md").is_file())


main()


def _bullet_slots(body: str) -> list[str]:
    """Every slot that asks for bullets, split down to the labels nested inside one `<fill:>`.

    A slot ends at the `>` that closes it — but `<name>` inside the prose closes it early, which is
    how the uncapped labels stayed invisible. Scan for the LAST `>` on the fill's own lines."""
    slots: list[str] = []
    for chunk in body.split("<fill:")[1:]:
        cut = chunk.rfind(">")
        block = chunk[:cut] if cut > 0 else chunk
        parts = re.split(r"\n(?=\*\*[A-Z])", block)
        slots += [" ".join(p.split()) for p in parts if "bullet" in p.lower()]
    return slots


def user_report_style() -> None:
    """User-facing reports are bullets under 20 words, not paragraphs.

    Owner's rule, 2026-08-29: these exist so a reader coming back COLD can rebuild context, decide,
    and move — not to carry every detail. The old rule was "fewer words wins", which is not a
    signal anyone can check, and the reports read as dense prose because the TEMPLATES asked for
    "plain sentences" in 100-250 word slots."""
    print("\nuser-facing reports — the templates ask for bullets, and every skill says so")
    root = Path(__file__).resolve().parent.parent
    skills = root / "superme_agent/harness/plugins/superme-dev/skills"

    # The agent copies the TEMPLATE, so the template is where shape is decided.
    agent_filled = {"triage", "review", "build", "close", "investigate"}
    for name in sorted(agent_filled):
        tpl = next((skills / name / "templates").glob("report-*template.md"))
        body = tpl.read_text(encoding="utf-8")
        ok(f"{name}: its slots ask for bullets", "bullet" in body.lower())
        ok(f"{name}: no slot still asks for 'plain sentences'", "plain sentences" not in body)
        # Per SLOT, not per file: investigate capped `What` and left `Proof`/`Do` open, and a
        # whole-file substring check let the capped slot vouch for its uncapped neighbours.
        for slot in _bullet_slots(body):
            ok(f"{name}: capped — {slot[:44]}", "under 20 words" in slot)

    # A rule nobody states is a rule nobody follows.
    for f in sorted(skills.glob("*/SKILL.md")):
        t = f.read_text(encoding="utf-8")
        if "Tone and style when writing" not in t:
            continue
        ok(f"{f.parent.name}: the tone block leads with the checkable rule",
           "Bullets, not paragraphs. One fact per bullet, each under 20 words." in t)
        ok(f"{f.parent.name}: the unmeasurable 'fewer words wins' is gone from it",
           "Fewer words wins" not in t.split("Tone and style when writing")[1].split("##")[0])



user_report_style()

def vet_lens_shape() -> None:
    """`What else was looked at` is one bullet per lens, and code refuses anything else.

    Live 2026-08-30: vet wrote its three lenses as one run-together paragraph. `_bold_lenses`
    only fires on a bullet opener, so it matched nothing and the slot rendered as a wall of prose
    with no label — a formatter that no-ops instead of complaining. The vet report is a
    format-string template, not a `<fill:>` one, so the template pass never reached it."""
    print("\nvet report — the lenses are bullets, and a paragraph is refused")
    from superme_agent.core.artifacts.user_reports import lens_slot_issues, _bold_lenses

    shipped = ("Intent: compared stats' new wiring against count's range pattern. Safety: stats "
               "only reads the ledger, no write path touched. Robustness: tried a reversed range "
               "and a malformed date, nothing unhandled found.")
    ok("the paragraph that actually shipped is refused", bool(lens_slot_issues(shipped)))
    ok("...and the refusal names the shape to use",
       "- Intent:" in lens_slot_issues(shipped)[0])
    ok("...and quotes the offending line back", "Intent: compared" in lens_slot_issues(shipped)[0])

    good = ("- Intent: mirrors the sibling command's range pattern; closes the stated gap.\n"
            "- Safety: read-only against the ledger, no write path touched.\n"
            "- Robustness: reversed and malformed ranges behaved like the siblings.")
    ok("one bullet per lens passes", not lens_slot_issues(good))
    ok("...and each lens name comes back bold", _bold_lenses(good).count("**") == 6)
    ok("...capitalized by CODE, not by whatever the author typed",
       _bold_lenses("- intent: mirrors the sibling.").startswith("- **Intent:**"))
    ok("a bullet whose body mentions a lens is not flagged",
       not lens_slot_issues("- Intent: checked it, and safety: nothing to touch."))
    ok("bullets naming no lens at all are refused",
       bool(lens_slot_issues("- Read the diff against the sibling commands.")))
    ok("an empty slot is not an error", not lens_slot_issues(""))
    # A lens the author already emphasised is still a lens opener. Refusing it would reject
    # correct input, and the surface has always accepted a pre-bolded name.
    for already in ("- **Safety:** already bold", "- *Robustness:* italic",
                    "- **Safety**: outside the colon"):
        ok(f"an emphasised lens is accepted: {already[:18]}", not lens_slot_issues(already))
    ok("...and bolding one stays idempotent",
       _bold_lenses("- **Safety:** already bold") == "- **Safety:** already bold")

    # The refusal has to land BEFORE the write, or a malformed report still reaches the owner.
    from superme_agent.core.artifacts.user_reports import write_vet_user_report
    with tempfile.TemporaryDirectory() as d:
        raised = ""
        try:
            write_vet_user_report(Path(d), None, summary="x", confirms="- ok", looked_at=shipped)
        except ValueError as e:
            raised = str(e)
        ok("the writer refuses rather than writing prose", bool(raised))
        ok("...and says nothing was written", "Nothing was written" in raised)
        ok("...and NO file was left behind", not list(Path(d).rglob("*")))


vet_lens_shape()

print(f"\nALL GREEN — {PASS} checks passed.")
