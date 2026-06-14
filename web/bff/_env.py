"""Load the single repo-root .env so the BFF (a standalone process, not importing the
Core) sees the shared config.

Imported for its side effect at the top of the BFF entrypoint and server modules,
before they read os.environ.
"""

from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]      # repo root (web/bff/_env.py -> ../../..)
load_dotenv(_ROOT / ".env")
