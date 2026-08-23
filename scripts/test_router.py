"""The router shell: the path grammar, and the state it replaced.

`parse` and `build` are each other's inverse for every real address. One writer per row is a
property of the SOURCE — a reintroduced `useState` would work perfectly while restoring the bug.

Run: PYTHONPATH=. python -m scripts.test_router
"""

import re

from scripts.sources import src

PASS = 0


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


# --- a faithful port of parse/build, kept honest by test_port_matches_source below ----------------
DEV_TABS = ["pipeline", "workspace", "project", "activity"]
STATS_TILES = ["tokens", "ops", "learning"]  # `?stats=` — a QUERY overlay, not a path (see below)
SURFACES = ["activity", "internals"]
# A QUERY overlay, because the popup opens OVER the surface you were on. Two of these were PAGES
# until it absorbed them.
CONFIG_SECTIONS = ["general", "learning", "identity", "constitution", "skills", "agents",
                   "psettings", "plearning", "partifacts", "pxray"]
LEGACY_SECTION = {"/config": "general", "/foundations": "identity"}
LEGACY_DEV_TAB = {"learning": "plearning", "artifacts": "partifacts", "promptxray": "pxray"}
PHASES = ["triage", "plan", "build", "vet", "investigate", "review", "close"]
# The drilldown's segments are `tab/sub`: the stepper is not clickable, so the address names the
# TAB. A phase appears as the Reports tab's sub.
ITEM_TABS = ["quick", "reports", "trace", "git"]
ITEM_SUBS = ["now", "deputy", "proof", "auth", "runs", "timeline"] + PHASES


def parse(pathname: str):
    seg = [s for s in pathname.split("/") if s]
    if not seg:
        return ("nexus",)
    if len(seg) == 1 and seg[0] in SURFACES:
        return ("surface", seg[0])
    if seg[0] == "repo" and len(seg) > 1:
        repo = seg[1]
        if len(seg) > 2 and seg[2] == "core":
            return ("core", repo)
        if len(seg) > 2 and seg[2] == "dev":
            tab = seg[3] if len(seg) > 3 else None
            return ("dev", repo, tab if tab in DEV_TABS else "pipeline")
        if len(seg) > 3 and seg[2] == "item":
            item = seg[3]
            if len(seg) > 4 and seg[4] == "pr":
                return ("pr", repo, item)
            tab = seg[4] if len(seg) > 4 and seg[4] in ITEM_TABS else None
            sub = seg[5] if tab and len(seg) > 5 and seg[5] in ITEM_SUBS else None
            return ("item", repo, item, tab, sub)
        if len(seg) == 2:
            return ("repo", repo)
    return ("nexus",)


def build(r) -> str:
    if r[0] == "nexus":
        return "/"
    if r[0] == "surface":
        return f"/{r[1]}"
    if r[0] == "repo":
        return f"/repo/{r[1]}"
    if r[0] == "core":
        return f"/repo/{r[1]}/core"
    if r[0] == "pr":
        return f"/repo/{r[1]}/item/{r[2]}/pr"
    if r[0] == "item":
        base = f"/repo/{r[1]}/item/{r[2]}"
        if not r[3]:
            return base
        return base + f"/{r[3]}" + (f"/{r[4]}" if r[4] else "")
    return f"/repo/{r[1]}/dev" + ("" if r[2] == "pipeline" else f"/{r[2]}")


def test_grammar() -> None:
    print("path grammar — every address round-trips, everything else collapses to /")
    addresses = [
        "/", "/activity", "/internals",
        "/repo/test-playground", "/repo/test-playground/core", "/repo/test-playground/dev",
        *[f"/repo/test-playground/dev/{t}" for t in DEV_TABS if t != "pipeline"],
        # The drilldown's three depths plus the PR page
        "/repo/test-playground/item/abc123",
        *[f"/repo/test-playground/item/abc123/{tb}" for tb in ITEM_TABS],
        "/repo/test-playground/item/abc123/quick/proof",
        "/repo/test-playground/item/abc123/quick/deputy",
        # Reports' sub is a phase — the one place a phase is still addressable.
        *[f"/repo/test-playground/item/abc123/reports/{ph}" for ph in PHASES],
        "/repo/test-playground/item/abc123/pr",
    ]
    for a in addresses:
        assert build(parse(a)) == a, f"{a} → {build(parse(a))}"
    ok(f"all {len(addresses)} real addresses round-trip parse→build unchanged")

    # Canonicalisation, not 404s: each renders something real, rewritten so a view has one
    # address.
    ok("`/repo/x/dev/pipeline` canonicalises to the bare `/repo/x/dev`",
       build(parse("/repo/x/dev/pipeline")) == "/repo/x/dev")
    ok("an unknown tab falls back to the workspace rather than 404ing",
       build(parse("/repo/x/dev/bogus")) == "/repo/x/dev")
    for junk in ("/nonsense", "/nonsense/deep/path", "/repo", "/repo/x/nope", "/activity/extra"):
        assert build(parse(junk)) == "/", f"{junk} → {build(parse(junk))}"
    ok("junk paths collapse to `/` (rendering the Nexus under a lying address is the failure)")

    # `activity` is BOTH a nav surface and a dev tab: two addresses, and a flattened table would
    # have collided them.
    ok("`/activity` is the global feed, `/repo/x/dev/activity` is the repo's — distinct",
       parse("/activity") == ("surface", "activity") and parse("/repo/x/dev/activity") == ("dev", "x", "activity"))


def test_item_grammar() -> None:
    """The drilldown's segments carry a meaning the dev tabs don't: an
    ABSENT tab is not a default someone chose, it is `whatever the drilldown opens on`."""
    print("item drilldown — tab/sub segments, and `pr` sharing the tab slot")
    ok("a bare item address leaves the tab UNSET",
       parse("/repo/x/item/i7") == ("item", "x", "i7", None, None))
    ok("naming a tab pins the view to it",
       parse("/repo/x/item/i7/reports") == ("item", "x", "i7", "reports", None))
    ok("a sub addresses one pane of one tab",
       parse("/repo/x/item/i7/quick/proof") == ("item", "x", "i7", "quick", "proof"))
    ok("Reports' sub is a PHASE — the one place a phase is still addressable",
       parse("/repo/x/item/i7/reports/build") == ("item", "x", "i7", "reports", "build"))

    # `pr` occupies the tab slot, safe only because both vocabularies are closed and `pr` is not a
    # tab.
    ok("`/item/:id/pr` is the PR page, never a tab", parse("/repo/x/item/i7/pr") == ("pr", "x", "i7"))
    ok("...and `pr` is not in the tab vocabulary, which is what makes that unambiguous",
       "pr" not in ITEM_TABS)

    ok("an unknown tab drops to the default rather than 404ing",
       build(parse("/repo/x/item/i7/bogus")) == "/repo/x/item/i7")
    # A sub is meaningless without its tab, so it is dropped rather than re-homed onto whatever is
    # open.
    ok("a sub without a valid tab is dropped, not re-homed",
       build(parse("/repo/x/item/i7/bogus/proof")) == "/repo/x/item/i7")
    ok("a sub that isn't in the vocabulary is dropped, keeping the tab",
       build(parse("/repo/x/item/i7/quick/nope")) == "/repo/x/item/i7/quick")
    ok("`/repo/x/item` with no id is not an address at all — it collapses to `/`",
       build(parse("/repo/x/item")) == "/")
    # Trailing junk TRUNCATES to the valid prefix, then canonicalises: the owner keeps the view
    # and the URL stops lying.
    ok("trailing junk truncates to the deepest valid address, consistent with `/dev/:tab`",
       build(parse("/repo/x/item/i7/quick/proof/extra")) == "/repo/x/item/i7/quick/proof"
       and build(parse("/repo/x/dev/project/extra")) == "/repo/x/dev/project")


def test_slice5_grammar() -> None:
    """Slice 5. Two shapes that look unlike each other but are the same idea: something that used to
    be an overlay flag now has an address."""
    print("stats tiles + the Pipeline tab's second pane")
    # The tiles live in the QUERY, not a path segment: a path displaced the surface underneath,
    # and an overlay belongs over where you are.
    ok("no tile is a PATH — a drill-in must not replace the surface it opens over",
       all(build(parse(f"/stats/{t}")) == "/" for t in STATS_TILES))
    app_src = src("web/frontend/src/App.tsx")
    ok("...they are read from `?stats=` instead", "useParam('stats')" in app_src)
    ok("...and closing is just dropping the param", "setParam('stats', null)" in app_src)

    # `workspace` is a PEER of `pipeline`: you look at one pane or the other, never one inside the
    # other.
    ok("the board is `/dev/workspace`, a sibling of `/dev`",
       parse("/repo/x/dev/workspace") == ("dev", "x", "workspace"))
    ok("...and the bare `/dev` is still the capture queue, so the landing pane did not move",
       build(parse("/repo/x/dev")) == "/repo/x/dev" and parse("/repo/x/dev")[2] == "pipeline")
    dash = src("web/frontend/src/features/dev/DevDashboard.tsx")
    ok("the dashboard derives the pane from the route rather than holding it",
       "const [zoom, setZoom] = useState" not in dash and "route.tab === 'workspace'" in dash)
    ok("an open item implies the board, so closing a drilldown lands on the card's pane",
       "route.name === 'item' ? 'workspace'" in dash)

    ws = src("web/frontend/src/features/dev/DevWorkspace.tsx")
    ok("both panes light the ONE Pipeline rail entry",
       "const pipelineTab = tab === 'pipeline' || tab === 'workspace'" in ws)


def test_config_overlay() -> None:
    """The System config popup. Five surfaces became one popup, addressed the same way the stats
    tiles are: a QUERY over whatever you were looking at, never a path that displaces it."""
    print("System config — a query overlay, and the addresses it inherited")
    ok("no section is a PATH — the popup must not replace the surface it opens over",
       all(build(parse(f"/config/{c}")) == "/" for c in CONFIG_SECTIONS))
    app_src = src("web/frontend/src/App.tsx")
    ok("...it is read from `?config=` instead", "useParam('config')" in app_src)

    # An old link must land on the same CONTENT, so arrival rewrites the address in place.
    ok("`/foundations` and `/config` are no longer surfaces",
       "foundations" not in SURFACES and "config" not in SURFACES)
    ok("...and each rewrites to the section that absorbed it",
       LEGACY_SECTION == {"/config": "general", "/foundations": "identity"})
    ok("a moved dev tab keeps its REPO in the path and puts the section in the query",
       set(LEGACY_DEV_TAB) == {"learning", "artifacts", "promptxray"}
       and all(v in CONFIG_SECTIONS for v in LEGACY_DEV_TAB.values()))
    ok("...so none of the three parses as a dev tab any more",
       all(build(parse(f"/repo/x/dev/{t}")) == "/repo/x/dev" for t in LEGACY_DEV_TAB))

    # An addressable section with no pane is a compile error, which stops the two lists drifting.
    cfg = src("web/frontend/src/features/config/SystemConfig.tsx")
    ok("the pane registry is exhaustive over the section vocabulary",
       "Record<ConfigSection, (repo: OrbitRepo, label: string) => ReactNode>" in cfg)


def test_port_matches_source() -> None:
    """The port above is only meaningful if it still mirrors the real matcher. Pin the two lists and
    the canonical-form rule against the source, so a change there fails HERE rather than silently
    leaving this suite testing a fiction."""
    print("the port mirrors the source (else this suite tests a fiction)")
    router_src = src("web/frontend/src/lib/router/index.ts")
    item_tabs_src = re.search(r"export const ITEM_TABS = \[(.*?)\] as const", router_src, re.S).group(1)
    ok("ITEM_TABS matches",
       [x.strip().strip("'") for x in item_tabs_src.split(",") if x.strip()] == ITEM_TABS)
    tabs = re.search(r"export const DEV_TABS = \[(.*?)\] as const", router_src, re.S).group(1)
    surfaces = re.search(r"export const SURFACES = \[(.*?)\] as const", router_src, re.S).group(1)
    ok("DEV_TABS matches", [t.strip().strip("'") for t in tabs.split(",") if t.strip()] == DEV_TABS)
    ok("SURFACES matches", [t.strip().strip("'") for t in surfaces.split(",") if t.strip()] == SURFACES)
    sections = re.search(r"export const CONFIG_SECTIONS = \[(.*?)\] as const", router_src, re.S).group(1)
    ok("CONFIG_SECTIONS matches",
       [t.strip().strip("'") for t in sections.split(",") if t.strip()] == CONFIG_SECTIONS)
    leg = re.search(r"const LEGACY_SECTION: Record<string, ConfigSection> = \{(.*?)\}", router_src, re.S).group(1)
    ok("LEGACY_SECTION matches",
       dict(re.findall(r"'([^']+)':\s*'([^']+)'", leg)) == LEGACY_SECTION)
    legt = re.search(r"const LEGACY_DEV_TAB: Record<string, ConfigSection> = \{(.*?)\}", router_src, re.S).group(1)
    ok("LEGACY_DEV_TAB matches",
       dict(re.findall(r"(\w+):\s*'([^']+)'", legt)) == LEGACY_DEV_TAB)
    tiles = re.search(r"export const STATS_TILES = \[(.*?)\] as const", router_src, re.S).group(1)
    ok("STATS_TILES matches", [t.strip().strip("'") for t in tiles.split(",") if t.strip()] == STATS_TILES)
    phases = re.search(r"export const PHASES = \[(.*?)\] as const", router_src, re.S).group(1)
    ok("PHASES matches", [t.strip().strip("'") for t in phases.split(",") if t.strip()] == PHASES)
    # Reports' subs ARE the phases, spread from one list, so adding a phase never means adding it
    # twice.
    subs = re.search(r"export const ITEM_SUBS = \[(.*?)\] as const", router_src, re.S).group(1)
    ok("ITEM_SUBS spreads PHASES rather than re-listing them",
       "...PHASES" in subs
       and [x.strip().strip("'") for x in subs.split(",") if x.strip() and "PHASES" not in x]
           == ["now", "deputy", "proof", "auth", "runs", "timeline"])

    # Forked at the root, so the tab carries none of the cockpit's polling. Addressable and
    # separate are independent decisions.
    entry = src("web/frontend/src/main.tsx")
    ok("the PR page still forks ABOVE App rather than becoming a route inside the shell",
       "route.name === 'pr' ? <PrPage" in entry)
    ok("...and a PR tab parked on the old `?repo=&pr=` form is rewritten in place, not broken",
       "legacyRepo && legacyItem" in entry and "replaceState" in entry)
    ok("`pipeline` is still the bare-`/dev` canonical form",
       "r.tab === 'pipeline' ? '' :" in router_src)
    ok("canonicalisation runs on EVERY navigation, not just at mount",
       "if (canonical !== window.location.pathname)" in router_src and "function refresh()" in router_src)
    ok("...and preserves the query string (the chat binding lives there)",
       "canonical + window.location.search" in router_src)


def test_old_state_is_gone() -> None:
    print("one writer per row: the replaced state is DELETED, not shadowed")
    app = src("web/frontend/src/App.tsx")
    ok("`active` is no longer component state", "useState('nexus')" not in app)
    ok("`dest` is no longer component state", "setDest(" not in app and "const [dest" not in app)
    ok("`selectedId` is gone — the inspector is the `/repo/:id` address",
       "setSelectedId(" not in app)
    ok("App reads the route instead", "const route = useRoute()" in app)

    ws = src("web/frontend/src/features/dev/DevWorkspace.tsx")
    ok("the dev tab is a prop from the route, not local state",
       "const [tab, setTab]" not in ws and "onTabChange" in ws)

    # Asserted ABSENT: reintroducing either would work perfectly while restoring the two-writers
    # defect. Matched on the DECLARATION, since a comment still names it.
    ok("`focusItem` is gone — the drilldown is an address, not a handed-over request",
       "const [focusItem," not in app and "focusItemId=" not in app)
    ok("gotoItem navigates straight to the item's address",
       "navigate({ name: 'item', repoId, itemId: hold.id, tab: null, sub: null })" in app)

    board = src("web/frontend/src/features/dev/DevDashboard.tsx")
    ok("the board reads the open item off the route", "const openId = route.name === 'item'" in board)
    ok("...and holds no `reviewId` of its own", "setReviewId" not in board and "const [reviewId" not in board)

    modal = src("web/frontend/src/features/dev/WorkItemModal.tsx")
    ok("the tab selection is the path, not state",
       "const [phaseView" not in modal and "const [tab," not in modal
       and "route.name === 'item' && route.tab" in modal)
    ok("...and so is the sub-tab", "const [sub, setSub]" not in modal)
    ok("the PR button links to the PR PATH, not the old query form",
       "build({ name: 'pr', repoId: contextId, itemId: it.id })" in modal and "&pr=" not in modal)

    ok("`drill` is no longer component state — the URL carries it",
       "const [drill, setDrill]" not in app and "const drill = useParam('stats')" in app)

    # The last local state, deliberately: the address needs a route that does not exist yet.
    act = src("web/frontend/src/features/activity/GlobalActivity.tsx")
    ok("`openRun` is still local — /run/:runId is blocked on a per-run GET",
       "const [openRun, setOpenRun]" in act)


def main() -> None:
    test_grammar()
    test_item_grammar()
    test_slice5_grammar()
    test_config_overlay()
    test_port_matches_source()
    test_old_state_is_gone()
    print(f"\nALL GREEN — {PASS} checks passed.")


if __name__ == "__main__":
    main()
