"""SuperMe web surface — a client of the Core daemon.

bff/       thin FastAPI backend-for-frontend (proxies knowledge HTTP, relays the chat
           WebSocket to the daemon; the place for any web-only aggregation later)
frontend/  React + Vite + TypeScript + Tailwind cockpit (talks only to /api)
"""
