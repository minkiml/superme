"""The web BFF — a network client of the Core daemon.

It exposes a same-origin /api surface to the frontend and forwards to the daemon, so the
frontend never has to know the daemon's address.
"""
