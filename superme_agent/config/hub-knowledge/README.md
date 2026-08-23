# The hub's anchor docs, as shipped

SuperMe's own `general/` anchor set. `setup_superme.py` copies this into the hub's knowledge home
on a fresh install, and never touches it again — the live copy is written by agents as the project
moves, so it is local state like `.env` and `repos.yaml` are.

Every other repo earns its anchor docs by being onboarded, because its owner can say what it is
for. Nobody arrives able to answer that about SuperMe itself, so this one ships.

Refresh it by copying the live `general/` back over this folder when the docs have moved on:

    python setup_superme.py --seed-hub

Keep it pointing rather than tabulating. These docs ship to people who will never edit them, and a
table of file paths is the first thing a refactor falsifies.
