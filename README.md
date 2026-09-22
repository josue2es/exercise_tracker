# Gym Tracker

Mobile-first workout tracker for a small invited group, plus a REST API and
MCP server for the owner's local LLM agents. One uvicorn process hosts the
NiceGUI UI, the REST API (`/api/v1`) and the MCP server (`/mcp`) as thin
adapters over a shared, transport-agnostic service layer. SQLite (WAL) stores
everything in one file.

- **People** log in with email + password (invite-only; Argon2id; login
  throttling).
- **Agents** authenticate with per-user API keys (`Authorization: Bearer gym_…`)
  against both REST and MCP. Read keys can't write; nobody can delete through
  REST or MCP.
- **Catalog**: 800+ exercises from [free-exercise-db](https://github.com/yuhonas/free-exercise-db)
  (public domain, images mirrored locally) and ~1,500 from
  [ExerciseDB V1](https://oss.exercisedb.dev/docs) (non-commercial, GIFs
  served from their CDN, attribution shown). Imported by CLI; the app never
  calls either source at runtime.

## Layout

| Path | Contents |
| --- | --- |
| `app.py` | Composition root (FastAPI + NiceGUI + MCP, lifespan, `/healthz`) |
| `config.py` | Settings from `.env` (see `.env.example`) |
| `db/` | Engine (SQLite pragmas), models, Alembic migrations |
| `services/` | Business logic: `users`, `api_keys`, `catalog`, `workouts`, `sessions`, `stats` |
| `api/` | REST router and request schemas (`/api/v1`, docs at `/api/docs`) |
| `mcp_server/` | 15 MCP tools (named to avoid shadowing the `mcp` package) |
| `ui/` | NiceGUI pages and components |
| `scripts/` | `import_catalog`, `backup_db`, `mcp_smoke_test` |
| `deploy/` | systemd units and the Caddy site block |
| `data/` | SQLite file, media, backups (git-ignored) |

## Setup

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                       # install pinned dependencies from uv.lock
cp .env.example .env          # then edit; chmod 600 .env
uv run alembic upgrade head   # create the schema
```

Generate a storage secret:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

On first start with `FIRST_ADMIN_EMAIL` set, a one-time setup link is printed
to the log; open it to create the admin account. (Migrations are also applied
automatically on an empty database.)

Import the exercise catalog (re-runnable and idempotent; ~2,300 exercises,
downloads ~1,700 images for free-exercise-db):

```bash
./import_catalog --source all        # or: free | exercisedb
./import_catalog --source all --no-images
```

## Running

```bash
uv run python -m app        # binds 127.0.0.1:8080 (see BIND_HOST/BIND_PORT)
uv run pytest               # 99 tests
```

## Deployment (Oracle ARM VPS + Caddy)

Same shape as Simple Forms: systemd service running the app on localhost,
Caddy in front on **gym.api-mcp.com**.

```bash
sudo cp deploy/gym-tracker.service /etc/systemd/system/
sudo cp deploy/gym-backup.service deploy/gym-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gym-tracker.service gym-backup.timer
```

Edit `deploy/Caddyfile`: replace `AGENT_IPS` with the fixed IPs of your agent
servers (the allowlist protects `/api/*` and `/mcp*`; the UI stays public),
then add the site block to Caddy. The Cloudflare record stays **DNS-only** so
Caddy sees real client IPs.

Deploy steps: `git pull && uv sync && uv run alembic upgrade head && sudo
systemctl restart gym-tracker`.

### Backups

- Nightly at 03:00 (`gym-backup.timer`): online SQLite backup (backup API, not
  a file copy) into `data/backups/`, keeping 14.
- Set `BACKUP_RSYNC_TARGET` in `.env` to copy each backup off the server
  (e.g. to the Hetzner VPS).
- Media and NiceGUI storage are excluded on purpose: media is re-downloadable
  via the import CLI, and lost storage only logs people out.

**Restore on a fresh host:** stop the service, copy the backup to
`DATABASE_PATH`, **delete any stale `gym.db-wal` / `gym.db-shm` files** (a
stale WAL from a previous run would overwrite restored pages), start the
service. Run `./import_catalog --source all` to re-download media. All
accounts, keys, workouts and history come back with the database.

## Agents

Create a key in **Settings → API keys** (label + scope; the full key is shown
once). Keys are `gym_` + 32 random bytes; the database stores only a SHA-256
hash. Deactivating a user blocks their keys immediately.

REST (docs at `/api/docs`):

```bash
curl -H "Authorization: Bearer $KEY" https://gym.api-mcp.com/api/v1/me
curl -H "Authorization: Bearer $KEY" 'https://gym.api-mcp.com/api/v1/exercises?q=bench&limit=5'
```

MCP (Streamable HTTP at exactly `/mcp`):

```json
{
  "mcpServers": {
    "gym": {
      "url": "https://gym.api-mcp.com/mcp",
      "headers": { "Authorization": "Bearer gym_..." }
    }
  }
}
```

Smoke test a running server:

```bash
uv run python -m scripts.mcp_smoke_test --url https://gym.api-mcp.com/mcp --key gym_...
```

Every write through REST or MCP is recorded in the audit log.

## Operation notes

- `/healthz` is unauthenticated for uptime checks.
- Logs go to journald (`journalctl -u gym-tracker -f`); the first-admin setup
  link is printed to the log once.
- Re-running the import with unchanged sources changes no row counts;
  exercises that disappear from a source are retired (hidden from the picker,
  still visible in history), never deleted.
- Sessions idle for over 6 hours close lazily the next time they're read.
- Tests cover user isolation (user B and admins get 404 for user A's data),
  key scopes, one-open-session, lazy idle close, retry-safe set logging, and
  the import pagination stop conditions.
