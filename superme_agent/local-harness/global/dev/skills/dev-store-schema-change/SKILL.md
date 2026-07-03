---
name: dev-store-schema-change
description: Executes the complete five-step procedure for shipping a dev-store schema change in the superme repo. Use when a column is added, renamed, or removed in dev_store.py or the .dev.db schema — prevents silent migration no-ops, missing decoder updates, and stale model.yaml entries that cause new fields to be invisible in API responses.
category: learned
---

# Dev-Store Schema-Change Procedure

Ships a column add, rename, or remove in `dev_store.py` without leaving the change half-wired or invisible to the cockpit.

## Steps

1. **Edit `dev_store.py` — CREATE TABLE guard (adding a column).** Ensure the statement reads `CREATE TABLE IF NOT EXISTS`. Immediately after it, add an idempotent `ALTER TABLE … ADD COLUMN` guard wrapped in a try/except so it is a no-op when the column already exists:

   ```python
   try:
       conn.execute("ALTER TABLE <table> ADD COLUMN <col> <type>")
   except Exception:
       pass  # column already exists — no-op
   ```

   A bare `CREATE TABLE` silently no-ops when the table exists, so without the `ALTER TABLE` fallback the new column never lands on an existing `.dev.db`.

   *For a rename:* SQLite does not support `ALTER TABLE … RENAME COLUMN` before v3.25. Use a migration: create a new column, copy data, update references in `_row_*`, then leave the old column (or recreate the table). Note the migration in `model.yaml`.

   *For a remove:* `ALTER TABLE … DROP COLUMN` requires SQLite ≥ 3.35. On older versions, recreate the table without the column. Either way, remove it from the `_row_*` decoder first (step 2) before touching the DB.

2. **Update the `_row_*` decoder.** Find the `_row_*` function for the target table and add (or remove) the column in the dict it returns. If this step is skipped, the column exists in the database but never appears in API responses — the cockpit stays blind to it.

3. **Bump `model.yaml`.** Open `superme_agent/config/model.yaml` (DATA SCHEMAS table) and add, update, or remove the entry for the changed column to keep schema documentation in sync.

4. **Restart the dev daemon.** The daemon caches module imports at startup; code changes to `dev_store.py` are not picked up without a restart. Start command:

   ```bash
   pkill -f "uvicorn.*8787" 2>/dev/null || true
   cd /path/to/superme && python -m uvicorn superme_agent.daemon.server:app --port 8787 --reload
   ```

   If the project ships a start script, use `scripts/start-daemon.sh` instead. Confirm the process is listening on port 8787 before proceeding.

5. **Verify the new field in the response.** Hit the relevant endpoint and confirm the column appears:

   ```bash
   curl -s http://localhost:8787/<endpoint> | jq '.<new_field>'
   ```

   If the field is `null` or absent: re-check step 1 (ALTER guard may not have run), then step 2 (decoder may be missing the field).

## Notes

- The two most common silent failures: (a) bare `CREATE TABLE` no-ops on existing DB — the column is never added; (b) `_row_*` decoder not updated — column exists in DB but never surfaces in responses. Neither raises an error.
- For renames and removes, the affected steps differ from an add — see the variants in steps 1–2 above.
