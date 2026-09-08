# Runtime data

These directories are persistent runtime storage and are intentionally not populated with database files in the source archive.

- `postgres/` — PostgreSQL data directory. **Do not delete or replace it on an existing installation.**
- `ntfy/` — ntfy persistent data, including `auth.db`. **Do not delete or replace it on an existing installation.**

The installer/Container Manager creates the directories automatically when they are absent. Existing production data must be preserved.
