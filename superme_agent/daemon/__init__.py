"""The SuperMe Core daemon — a FastAPI service that fronts the Core for surfaces.

One long-running local process; surfaces connect as clients over a bidirectional WebSocket for
agent turns, plus HTTP for everything else.
"""
