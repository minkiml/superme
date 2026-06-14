"""The web BFF — a network client of the Core daemon.

It exposes a same-origin /api surface to the React frontend and forwards to the
daemon: knowledge endpoints over HTTP, and the agent chat over a WebSocket relay.
Surface-specific aggregation/visualization endpoints would live here later; for now
it's a thin passthrough so the frontend never talks to the daemon directly.
"""
