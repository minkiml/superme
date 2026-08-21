"""WorkGraph — the DERIVED graph projection over the workspace.

There is deliberately NO stored DAG: every feed (roadmap, item yaml, inbox, git) already has an
authoritative home, and storing the graph would be a four-way sync problem. This reads prepared
inputs and assembles a typed graph on demand.

Nodes: `repo_root` · `deliverable` · `work_item` · `inbox_spawn`.
Edges: `contains` · `spawned_from` · `supersedes`. Git state is DECORATION, never a node.
"""


class WorkGraph:
    """A tiny directed graph: nodes {id → dict}, edges [{src, dst, kind, …}]."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self._out: dict[str, list[str]] = {}
        self._in: dict[str, list[str]] = {}

    def add_node(self, node_id: str, kind: str, **attrs) -> None:
        self.nodes.setdefault(node_id, {"id": node_id, "kind": kind, **attrs})

    def add_edge(self, src: str, dst: str, kind: str, **attrs) -> None:
        if src not in self.nodes or dst not in self.nodes:
            return  # dangling ref — decoration, never a crash
        self.edges.append({"src": src, "dst": dst, "kind": kind, **attrs})
        self._out.setdefault(src, []).append(dst)
        self._in.setdefault(dst, []).append(src)

    def _walk(self, start: str, adj: dict[str, list[str]]) -> list[str]:
        seen: list[str] = []
        stack = list(adj.get(start, []))
        while stack:
            n = stack.pop()
            if n not in seen:
                seen.append(n)
                stack.extend(adj.get(n, []))
        return seen

    def descendants(self, node_id: str) -> list[str]:
        return self._walk(node_id, self._out)

    def ancestors(self, node_id: str) -> list[str]:
        return self._walk(node_id, self._in)

    def topo(self) -> list[str] | None:
        """Kahn topological order, or None when the graph has a cycle."""
        indeg = {n: 0 for n in self.nodes}
        for e in self.edges:
            indeg[e["dst"]] += 1
        queue = sorted(n for n, d in indeg.items() if d == 0)
        order: list[str] = []
        while queue:
            n = queue.pop(0)
            order.append(n)
            for m in self._out.get(n, []):
                indeg[m] -= 1
                if indeg[m] == 0:
                    queue.append(m)
        return order if len(order) == len(self.nodes) else None

    def cycles(self) -> list[list[str]]:
        """The node sets stuck in cycles (empty = acyclic). Reported, never raised — a hand-edited
        cycle must surface as data, not crash the endpoint."""
        indeg = {n: 0 for n in self.nodes}
        for e in self.edges:
            indeg[e["dst"]] += 1
        queue = [n for n, d in indeg.items() if d == 0]
        removed = set()
        while queue:
            n = queue.pop()
            removed.add(n)
            for m in self._out.get(n, []):
                indeg[m] -= 1
                if indeg[m] == 0 and m not in removed:
                    queue.append(m)
        stuck = [n for n in self.nodes if n not in removed]
        return [stuck] if stuck else []


def item_node_id(item_id: str) -> str:
    return f"item:{item_id}"


def build(*, repo_id: str, items: list[dict], deliverables: list[dict],
          waves: list[dict], inbox_rows: list[dict]) -> WorkGraph:
    """Assemble the graph from the authoritative feeds: work-item dicts, PRD deliverables,
    roadmap waves (resolving an item's wave to its deliverable), and OPEN spawned inbox rows."""
    g = WorkGraph()
    root = f"repo:{repo_id}"
    g.add_node(root, "repo_root", label=repo_id)
    wave_to_deliverable = {w["id"]: w.get("deliverable") for w in waves}

    for d in deliverables:
        nid = f"deliverable:{d['id']}"
        g.add_node(nid, "deliverable", label=d.get("title") or d["id"], slug=d["id"])
        g.add_edge(root, nid, "contains")

    for it in items:
        nid = item_node_id(str(it.get("id")))
        g.add_node(nid, "work_item", label=it.get("title") or it.get("id"),
                   item_kind=it.get("kind"), phase=it.get("phase"), status=it.get("status"),
                   outcome=it.get("outcome"),
                   # Straight off the item record: the graph must render without touching git.
                   git_branch=it.get("git_branch"),
                   git_merged=bool(it.get("git_merge_commit")))
        d_slug = it.get("deliverable") or wave_to_deliverable.get(it.get("wave"))
        if d_slug and f"deliverable:{d_slug}" in g.nodes:
            g.add_edge(f"deliverable:{d_slug}", nid, "contains")

    # Grouping fallback + relations, after all item nodes exist.
    for it in items:
        nid = item_node_id(str(it.get("id")))
        sf = it.get("spawned_from") if isinstance(it.get("spawned_from"), dict) else None
        if sf and sf.get("item"):
            g.add_edge(nid, item_node_id(str(sf["item"])), "spawned_from",
                       relation=sf.get("relation"))
        grouped = any(e["dst"] == nid and e["kind"] == "contains" for e in g.edges)
        if not grouped and not sf:
            g.add_edge(root, nid, "contains")   # ungrouped original hangs off the repo root
        if it.get("superseded_by"):
            g.add_edge(nid, item_node_id(str(it["superseded_by"])), "supersedes")

    for r in inbox_rows:
        sf = r.get("spawned_from")
        if r.get("status") == "open" and isinstance(sf, dict) and sf.get("item"):
            nid = f"inbox:{r['id']}"
            g.add_node(nid, "inbox_spawn", label=r.get("title") or (r.get("text") or "")[:60],
                       inbox_id=r["id"])
            g.add_edge(nid, item_node_id(str(sf["item"])), "spawned_from",
                       relation=sf.get("relation"))
    return g
