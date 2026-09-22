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

## Runbook

Common operations tasks. Unless noted otherwise, commands run in
`/home/ubuntu/exercise_tracker` with the service venv
(`.venv/bin/python`), and the service is restarted afterwards:
`sudo systemctl restart gym-tracker`.

### Rotate the storage secret

Do this if `STORAGE_SECRET` may have leaked. **Effect: every browser session
is logged out** (UI only; API keys keep working).

1. Generate a new secret:
   `python -c "import secrets; print(secrets.token_hex(32))"`
2. Put it in `.env` as `STORAGE_SECRET`.
3. Restart the service. Everyone logs in again; nothing else is lost.

### Reset a user's password

Phase 1 has no admin password reset (by design — admins manage accounts, not
credentials). If a user is locked out, set a temporary password directly and
ask them to change it in Settings:

```bash
sudo systemctl stop gym-tracker
.venv/bin/python - <<'EOF'
import os
os.environ.setdefault("DATABASE_PATH", "data/gym.db")
from db.session import configure, get_session
configure("sqlite:///" + os.path.abspath(os.environ["DATABASE_PATH"]))
from db.models import User
from services.users import hash_password
with get_session() as s:
    from sqlalchemy import select as _select
    user = s.scalars(_select(User).where(User.email == "them@example.com")).one()
    user.password_hash = hash_password("temporary-password")
print("password reset")
EOF
sudo systemctl start gym-tracker
```

### Revoke a compromised API key

The key's owner can revoke it in Settings → API keys. If they can't (lost
laptop with a stored key), any of these works:

- **Deactivate the user** in Admin (blocks login, UI sessions and all their
  keys at once), then reactivate after the key is revoked.
- Revoke the key directly in the database (safe while the service runs):

```bash
.venv/bin/python - <<'EOF'
import os
os.environ.setdefault("DATABASE_PATH", "data/gym.db")
from db.session import configure, get_session
configure("sqlite:///" + os.path.abspath(os.environ["DATABASE_PATH"]))
from db.models import ApiKey, utcnow
with get_session() as s:
    from sqlalchemy import select as _select
    for key in s.scalars(_select(ApiKey).where(ApiKey.revoked_at.is_(None))):
        print(key.id, key.label, key.key_prefix, "…", key.scopes)
    key_id = int(input("key id to revoke: "))
    s.get(ApiKey, key_id).revoked_at = utcnow()
print("revoked")
EOF
```

### Add or change agent IPs

Edit `AGENT_IPS` in the Caddy site block (`deploy/Caddyfile`), reload Caddy
(`sudo systemctl reload caddy`). No app restart needed; agents on this host
can always call `127.0.0.1:8080` directly.

### Refresh exercise media

Media files are re-downloadable; the database never depends on them:

```bash
rm -rf data/media/free-exercise-db && ./import_catalog --source free
```

### Check what an agent has been doing

Every REST/MCP write and every admin action is in the audit log:

```bash
.venv/bin/python - <<'EOF'
import os
os.environ.setdefault("DATABASE_PATH", "data/gym.db")
from db.session import configure, get_session
configure("sqlite:///" + os.path.abspath(os.environ["DATABASE_PATH"]))
from db.models import AuditLog
with get_session() as s:
    from sqlalchemy import select as _select
    for row in s.scalars(_select(AuditLog).order_by(AuditLog.id.desc()).limit(30)):
        print(row.created_at, row.actor, f"user={row.user_id}", row.action, row.target or "")
EOF
```

### Upgrade the app

```bash
git pull && uv sync && uv run alembic upgrade head
sudo systemctl restart gym-tracker
```

### Move to a new host

1. New host: clone the repo, `uv sync`, copy `.env` (or edit it).
2. Restore the latest backup per the restore procedure above.
3. Re-download media: `./import_catalog --source all`.
4. Install the systemd units + Caddy block, start everything, check `/healthz`.

### Verify the running system

```bash
systemctl status gym-tracker           # service up?
curl -s http://127.0.0.1:8080/healthz  # app healthy?
ls -lt data/backups | head -3          # backups recent?
systemctl list-timers gym-backup.timer # backup scheduled?
```
