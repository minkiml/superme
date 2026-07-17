"""Response schemas for the gate-brief + lifecycle routes (routers/dev/gates.py — S6/D8/D10)."""

from __future__ import annotations

from pydantic import BaseModel


class GateCheck(BaseModel):
    """One mechanical row of a gate's evaluation — computed from durable state, never a claim."""
    criterion: str
    ok: bool
    detail: str


class GateOption(BaseModel):
    id: str
    label: str
    consequence: str


class GateDecision(BaseModel):
    """The uniform decision block (D10 ★): recommendation FIRST, stakes one line, per-option
    consequence, dual-scale effort."""
    recommendation: str
    stakes: str
    options: list[GateOption]
    effort_user: str
    effort_agent: str


class GateBriefResponse(BaseModel):
    """One gate's full decision surface. `brief` is the rendered markdown (continuity → delta →
    narrative → decision); `at_gate: False` means the item is mid-phase and this previews the
    NEXT gate. Answerable without opening code — that is the contract."""
    id: str
    gate: str      # triage-exit | pre-main | deliver | close
    at_gate: bool
    phase: str
    title: str
    brief: str
    decision: GateDecision
    checks: list[GateCheck]


class AbandonResponse(BaseModel):
    """The abandon brief (D8): what was torn down + the children triage list. Blocking children
    existed only for this parent — the owner disposes each (abandon / promote to independent);
    parallel children continue untouched."""
    ok: bool
    id: str
    outcome: str   # abandoned | superseded
    worktree_removed: bool | None = None
    session_cleared: bool
    runs_freed: int
    blocking_children: list[str]
    parallel_children: list[str]
