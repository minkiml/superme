"""A work-item's title is a label, and every path that mints one must keep it that way.

No particular wording is pinned — only that a bad title is refused and a good one survives.

Run: PYTHONPATH=. python -m scripts.test_titles
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from superme_agent.core.dev_knowledge import DevKnowledgeService      # noqa: E402
from superme_agent.core.vocab.titles import TITLE_MAX, check_title, normalize_title  # noqa: E402

PASS = FAIL = 0


def ok(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


# --------------------------------------------------------------------------- the rule


def test_a_title_that_reads_as_a_label_is_left_alone():
    for good in ("Add dark mode support", "tally list --csv", "Night theme for accessibility",
                 "Fold the two parked-item readers into one"):
        ok(check_title(good) == "", f"{good!r} should pass")
        ok(normalize_title(good) == good, f"{good!r} should survive normalization unchanged")


def test_the_four_ways_a_title_stops_being_a_label():
    ok(check_title("") != "", "empty is refused")
    ok(check_title("   \n  ") != "", "whitespace-only is refused")
    ok(check_title("x" * (TITLE_MAX + 1)) != "", "over the cap is refused")
    ok(check_title("Add dark mode support.") != "", "a closing period is refused")
    ok(check_title("Fix it", description="Fix it") != "", "a copy of the description is refused")
    ok(check_title("Fix it", description="Something else") == "",
       "a title that merely resembles nothing is fine")


def test_a_complaint_says_what_to_do():
    # Delivered as a tool error, it is the only instruction the agent gets, so it must name the
    # field.
    for bad in ("", "x" * 99, "Ends in a period."):
        ok("title" in check_title(bad).lower(), f"the complaint for {bad[:12]!r} names the field")


# --------------------------------------------------------------------------- the floor


def test_a_pasted_request_degrades_to_its_first_sentence():
    ask = ("tally search — find entries by note text. I log a lot of notes (--note) and can never "
           "find them again. I want it to scan the whole ledger.")
    got = normalize_title(ask)
    ok(got == "tally search — find entries by note text", f"first sentence, got {got!r}")
    ok(check_title(got) == "", "and the result itself passes the rule")


def test_a_first_sentence_that_is_still_too_long_is_cut_on_a_word():
    got = normalize_title("Add a really quite remarkably long and thoroughly overspecified feature "
                          "to the command line interface")
    ok(len(got) <= TITLE_MAX, f"within the cap, got {len(got)}")
    ok(got.endswith("…"), "and shows that it was cut")
    ok("  " not in got and not got[:-1].endswith(" "), "no dangling space before the ellipsis")


def test_the_floor_never_raises():
    for junk in (None, "", 12345, "\n\n\n", "."):
        normalize_title(junk)                      # the point is that none of these blow up
    ok(True, "normalize_title survives junk")


# --------------------------------------------------------------------------- the mint points


def _svc():
    root = Path(tempfile.mkdtemp())
    (root / "work-items").mkdir(parents=True, exist_ok=True)
    return DevKnowledgeService(), root


def test_an_untitled_push_cannot_land_a_paragraph_as_a_title():
    dev, root = _svc()
    try:
        ask = ("tally search — find entries by note text. I log a lot of notes and can never find "
               "them again, so I want a search command.")
        # inbox_flow passes `title or text` — an owner who typed no title sends the body as both.
        wi = dev.create_work_item(root, title=ask, description=ask, kind="implementation")
        got = str((dev.read_work_item(root, wi["id"]) or {}).get("title") or "")
        ok(len(got) <= TITLE_MAX, f"the stored title is within the cap, got {len(got)}")
        ok(got == "tally search — find entries by note text", f"and reads as a label: {got!r}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_itemization_refuses_a_bad_title_instead_of_repairing_it():
    dev, root = _svc()
    try:
        try:
            dev.itemize_launch(root, [{"key": "d-1", "title": "Do the thing." * 9,
                                       "description": "…"}])
            ok(False, "a bad itemized title should raise")
        except ValueError as e:
            ok("title" in str(e).lower(), "and the error names the field")
            ok("d-1" in str(e), "and names which item, since a cohort is many")
        ok(not list((root / "work-items").iterdir()), "and nothing was created")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_triage_can_rename_and_a_no_op_rename_is_free():
    dev, root = _svc()
    try:
        wi = dev.create_work_item(root, title="Some vague thing", description="…", kind="implementation")
        ok(dev.set_work_item_title(root, wi["id"], "Add dark mode support") is True,
           "a real rename reports a change")
        ok(str((dev.read_work_item(root, wi["id"]) or {}).get("title")) == "Add dark mode support",
           "and lands on disk")
        ok(dev.set_work_item_title(root, wi["id"], "Add dark mode support") is False,
           "passing the same title back is a no-op")
        try:
            dev.set_work_item_title(root, wi["id"], "Nope." * 40)
            ok(False, "a bad rename should raise")
        except ValueError:
            ok(True, "a bad rename raises")
        ok(str((dev.read_work_item(root, wi["id"]) or {}).get("title")) == "Add dark mode support",
           "and leaves the old title standing")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_rename_touches_nothing_but_the_title():
    # The whole reason renaming is safe: identity is the folder/id, never the label.
    dev, root = _svc()
    try:
        wi = dev.create_work_item(root, title="Before", description="the body", kind="implementation")
        before = dev.read_work_item(root, wi["id"]) or {}
        dev.set_work_item_title(root, wi["id"], "After")
        after = dev.read_work_item(root, wi["id"]) or {}
        for field in ("id", "root_id", "parent_id", "kind", "phase", "status", "created_at"):
            ok(before.get(field) == after.get(field), f"{field} is untouched by a rename")
        ok((root / "work-items" / wi["id"]).is_dir(), "the folder is still the id")
        ok("the body" in (root / "work-items" / wi["id"] / "item.md").read_text(encoding="utf-8"),
           "and the description survives")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    for fn in sorted([v for k, v in globals().items() if k.startswith("test_")],
                     key=lambda f: f.__code__.co_firstlineno):
        print(f"· {fn.__name__}")
        fn()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
