"""The SuperMe Core daemon — a FastAPI service that fronts the Core for surfaces.

One long-running local process; surfaces (web BFF in Stage C, Slack in B2) connect as
clients. Stage B1 exposes a single bidirectional WebSocket for agent turns:

  client -> daemon:  turn, approval_response
  daemon -> client:  text_delta, status, approval_request, result, error

Knowledge HTTP endpoints arrive in Stage C with the Knowledge service.
"""
