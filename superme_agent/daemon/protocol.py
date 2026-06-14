"""WebSocket frame protocol for the agent socket.

All frames are JSON objects with a "type". The surface-neutral TurnEvents map to
daemon->client frames; the client sends turn + approval_response frames back.

  client -> daemon
    {"type":"turn", "prompt":str, "context_id":str|null, "resume":str|null, "model":str|null}
    {"type":"approval_response", "id":str, "approved":bool}

  daemon -> client
    {"type":"init", "slash_commands":[str], "model":str|null}
    {"type":"text_delta", "text":str}
    {"type":"status", "tool_name":str, "tool_input":obj}
    {"type":"approval_request", "id":str, "tool_name":str, "tool_input":obj}
    {"type":"result", "text":str, "model":str|null, "context_pct":int|null,
                      "context_window":int|null, "session_id":str|null}
    {"type":"error", "message":str}
"""

from ..core.events import Init, TextDelta, Status, Result, TurnEvent


def event_to_frame(ev: TurnEvent) -> dict:
    """Serialize a Core TurnEvent into a daemon->client frame."""
    if isinstance(ev, Init):
        return {"type": "init", "slash_commands": ev.slash_commands, "model": ev.model}
    if isinstance(ev, TextDelta):
        return {"type": "text_delta", "text": ev.text}
    if isinstance(ev, Status):
        return {"type": "status", "tool_name": ev.tool_name, "tool_input": ev.tool_input}
    if isinstance(ev, Result):
        return {
            "type": "result",
            "text": ev.text,
            "model": ev.model,
            "context_pct": ev.context_pct,
            "context_window": ev.context_window,
            "session_id": ev.session_id,
        }
    raise TypeError(f"unknown TurnEvent: {ev!r}")
