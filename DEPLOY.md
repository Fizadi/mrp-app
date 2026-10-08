# Docker Deployment — MRP Flask + SQL Server

This project runs as a self-contained Docker stack: **Flask (Gunicorn)** + **SQL Server 2022**.
The database is restored from `backups/MRP_database.bak` on first boot.

---

## 1. Prerequisites

- **Docker Desktop for Windows** installed and running.
  Verify:
  ```powershell
  docker --version
  docker compose version
  docker info
  ```
- **`backups/MRP_database.bak`** present in the project root.
- **`.env`** created from `.env.example` with real values.
- **Ports 5001 and 1433** free on the host (stop local Flask / SQL Express first).

---

## 2. One-Time Setup

From the project root:

```powershell
cd "D:\LOGISTICS PROJECT, 2024-02-01\MRP PROJECT\FLASKAPP"

# Create .env from template
Copy-Item .env.example .env
notepad .env          # fill SECRET_KEY, DB_PASSWORD, MSSQL_SA_PASSWORD, AI_API_KEY

# Confirm the .bak is in place
Test-Path .\backups\MRP_database.bak    # must print True
```

---

## 3. Build & Start

### Production mode (default CMD, no source bind-mount)

Temporarily rename the override file so Docker Compose ignores it:

```powershell
Rename-Item docker-compose.override.yml docker-compose.override.yml.bak

docker compose build
docker compose up -d
docker compose logs -f web
```

### Development mode (bind-mounted source, Gunicorn --reload)

```powershell
Rename-Item docker-compose.override.yml.bak docker-compose.override.yml

docker compose build
docker compose up -d
docker compose logs -f web
```

### What happens on first boot

1. `sqlserver` container starts SQL Server 2022.
2. Healthcheck runs `SELECT 1` until the server is ready.
3. `web` container's `entrypoint.sh` starts and waits for SQL Server.
4. Since `MRP_database` doesn't exist, it restores `backups/MRP_database.bak`.
5. It creates the `mrp_user` login + DB user + `db_owner` role.
6. Gunicorn starts on port `5001`.

Expected log sequence:

```
⏳ Waiting for SQL Server at sqlserver:1433 ...
✅ SQL Server ready (attempt N).
📦 Restoring MRP_database from /app/backups/MRP_database.bak ...
   → data logical: <name>
   → log  logical: <name>
✅ Restore complete.
🔐 Ensuring login [mrp_user] ...
✅ Login/user ready.
🚀 Starting: gunicorn ...
```

Open http://localhost:5001

---

## 4. Day-to-Day Commands

| Task | Command |
|------|---------|
| Follow app logs | `docker compose logs -f web` |
| Follow DB logs | `docker compose logs -f sqlserver` |
| Stop (keep data) | `docker compose stop` |
| Start again | `docker compose start` |
| Restart app only | `docker compose restart web` |
| Restart DB only | `docker compose restart sqlserver` |
| Full shutdown (keep volumes) | `docker compose down` |
| Full shutdown + delete volumes | `docker compose down -v` |
| Rebuild after dependency change | `docker compose build --no-cache web` then `docker compose up -d` |
| Shell inside app container | `docker compose exec web bash` |
| Shell inside DB container | `docker compose exec sqlserver bash` |

---

## 5. Dev Workflow (with override active)

When `docker-compose.override.yml` is present:

- Your local `./app`, `./run.py`, `./config.py` are **bind-mounted** into the container.
- Gunicorn runs with `--reload`, so editing a `.py` file auto-restarts the worker.
- Changes to `requirements.txt`, `Dockerfile`, or `entrypoint.sh` **require a rebuild**:
  ```powershell
  docker compose build web
  docker compose up -d
  ```

---

## 6. Connecting to the Containerized SQL Server

Using `sqlcmd` from inside the DB container:

```powershell
docker exec -it mrp_sqlserver /opt/mssql-tools18/bin/sqlcmd `
  -S localhost -U sa -P "your_sa_password" -C
```

List databases:

```powershell
docker exec -it mrp_sqlserver /opt/mssql-tools18/bin/sqlcmd `
  -S localhost -U sa -P "your_sa_password" -C `
  -Q "SELECT name FROM sys.databases;"
```

List tables in `MRP_database`:

```powershell
docker exec -it mrp_sqlserver /opt/mssql-tools18/bin/sqlcmd `
  -S localhost -U sa -P "your_sa_password" -C -d MRP_database `
  -Q "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE';"
```

---

## 7. Connecting SSMS from Windows to the Container

With the **dev override** active (`1433:1433` exposed):

1. Open SSMS
2. Server name: `localhost,1433`
3. Authentication: **SQL Server Authentication**
4. Login: `sa`
5. Password: your `MSSQL_SA_PASSWORD` from `.env`
6. **Options >> → Connection Properties → Trust server certificate:** ✅
7. Connect

With **production mode** (no override), SQL Server is **not** exposed to the host. To connect anyway:

```powershell
# In a separate PowerShell window (keeps port-forward open):
docker exec -it mrp_sqlserver /opt/mssql-tools18/bin/sqlcmd `
  -S localhost -U sa -P "your_sa_password" -C
```

---

## 8. Data Persistence

Three named Docker volumes hold all persistent data:

| Volume | Mount | Contents |
|--------|-------|----------|
| `mssql_data` | `/var/opt/mssql` | SQL Server database files (.mdf, .ldf) |
| `uploads_data` | `/app/static/uploads` | User-uploaded files |
| `generated_docs_data` | `/app/static/documents/generated` | Generated HTML documents |

Inspect them:

```powershell
docker volume ls
docker volume inspect flaskapp_mssql_data
docker volume inspect flaskapp_uploads_data
docker volume inspect flaskapp_generated_docs_data
```

**They survive `docker compose down`.** Only `docker compose down -v` deletes them.

### Backup the running DB (recommended before major changes)

```powershell
docker exec -it mrp_sqlserver /opt/mssql-tools18/bin/sqlcmd `
  -S localhost -U sa -P "your_sa_password" -C `
  -Q "BACKUP DATABASE [MRP_database] TO DISK=N'/var/opt/mssql/backup/MRP_database_$(Get-Date -Format yyyyMMdd).bak' WITH INIT;"
```

Then copy it out:

```powershell
docker cp mrp_sqlserver:/var/opt/mssql/backup/MRP_database_20261001.bak .\backups\
```

---

## 9. Troubleshooting

### `web` container exits immediately

```powershell
docker compose logs web
```

Common causes:

| Log line | Fix |
|----------|-----|
| `DB_PASSWORD is required` | `.env` missing `DB_PASSWORD` |
| `Login failed for user 'mrp_user'` | entrypoint's login-creation step failed — check DB logs |
| `sqlcmd not found` | SQL Server image tag changed path — check `docker exec mrp_sqlserver ls /opt/` |
| `Could not parse logical names from .bak` | `.bak` is corrupt — recreate with `BACKUP DATABASE` |
| `Operating system error 5(Access is denied.)` | `.bak` permissions — ensure `backups/` is readable |

### Port 5001 or 1433 already in use

```powershell
# Find what's using it
Get-NetTCPConnection -LocalPort 5001 | Select-Object OwningProcess
Get-NetTCPConnection -LocalPort 1433 | Select-Object OwningProcess

# Stop local Flask dev server, or SQL Server Express service:
Stop-Service 'MSSQL$SQLEXPRESS'
```

Or change host ports in `docker-compose.yml`:

```yaml
ports:
  - "5002:5001"    # access at localhost:5002
```

### Restore fails: "The backup set holds a backup of a database other than the existing"

The marker file `backups/.restored_MRP_database` may exist but the DB is empty. Remove it and retry:

```powershell
Remove-Item .\backups\.restored_MRP_database -ErrorAction SilentlyContinue
docker compose down -v
docker compose up -d
```

### Restore fails: "Cannot open backup device"

The `.bak` isn't visible inside the container:

```powershell
docker compose exec web ls -la /app/backups/
```

If empty, the `./backups` mount failed — check `docker-compose.yml`:

```yaml
volumes:
  - ./backups:/app/backups:ro
```

### Gunicorn "Working outside of application context"

`app:create_app()` is being called at import time by something other than Gunicorn. Ensure `run.py` is **not** imported by the Gunicorn target. In `docker-compose.override.yml`, the `command:` should end with `"app:create_app()"`.

### App loads but shows "Login failed for user 'mrp_user'"

The login wasn't created. Run manually:

```powershell
docker exec -it mrp_sqlserver /opt/mssql-tools18/bin/sqlcmd `
  -S localhost -U sa -P "your_sa_password" -C -Q "
    USE master;
    IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name='mrp_user')
        CREATE LOGIN mrp_user WITH PASSWORD=N'YourStrongPassword123!', CHECK_POLICY=OFF;
    ALTER LOGIN mrp_user ENABLE;
    GRANT CONNECT SQL TO mrp_user;
    USE MRP_database;
    IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name='mrp_user')
        CREATE USER mrp_user FOR LOGIN mrp_user;
    ALTER ROLE db_owner ADD MEMBER mrp_user;
"
```

---

## 10. Deploying to Liara VPS (Debian + Docker)

Liara's "Docker" one-click app gives you a Debian VPS with Docker pre-installed.
You run `docker compose` yourself on it.

### 10.1 Build the image locally

```powershell
docker compose -f docker-compose.yml build web
docker tag flaskapp-web:latest your-dockerhub-username/mrp-flask:1.0.0
docker login
docker push your-dockerhub-username/mrp-flask:1.0.0
```

### 10.2 SSH into the Liara VPS

```bash
ssh root@your-liara-vps-ip
```

### 10.3 Install Docker (if not pre-installed)

```bash
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin
```

### 10.4 Copy the stack files to the VPS

From your Windows laptop:

```powershell
scp docker-compose.yml root@your-liara-vps-ip:/opt/mrp/docker-compose.yml
scp .env root@your-liara-vps-ip:/opt/mrp/.env
scp -r backups root@your-liara-vps-ip:/opt/mrp/backups
```

Or use `git clone` if your project is on GitHub.

### 10.5 Pull the image and start

On the VPS:

```bash
cd /opt/mrp
docker compose pull web
docker compose up -d
docker compose logs -f web
```

### 10.6 Update to a new version

From Windows:

```powershell
docker build -t your-dockerhub-username/mrp-flask:1.0.1 .
docker push your-dockerhub-username/mrp-flask:1.0.1
```

On VPS — edit `docker-compose.yml` to reference `:1.0.1`, then:

```bash
docker compose pull web
docker compose up -d web
```

### 10.7 Firewall

Only expose port 5001 publicly. SQL Server (1433) must stay internal.

```bash
ufw allow 22
ufw allow 5001
ufw enable
```

---

## 11. Files Reference

| File | Purpose |
|------|---------|
| `Dockerfile` | Flask image: Python 3.12 + ODBC Driver 17 + Gunicorn |
| `docker-compose.yml` | Production stack: `sqlserver` + `web` |
| `docker-compose.override.yml` | Dev overrides: bind-mounts + `--reload` |
| `entrypoint.sh` | Waits for SQL, restores `.bak`, creates `mrp_user`, starts Gunicorn |
| `.env` | Real secrets (never commit) |
| `.env.example` | Template (safe to commit) |
| `.dockerignore` | Files excluded from build context |
| `backups/MRP_database.bak` | Initial DB snapshot |
| `config.py` | Flask config, reads from env vars |
| `app/core/database.py` | pyodbc connection helper, reads from env vars |

---

## 12. Quick Reference Card

```powershell
# First time
Copy-Item .env.example .env
notepad .env
docker compose build
docker compose up -d

# Daily
docker compose logs -f web
docker compose restart web

# Code change (dev override active)
# — nothing needed, Gunicorn --reload picks it up
# — if you changed requirements.txt:
docker compose build web
docker compose up -d

# Nuke and restart from scratch
docker compose down -v
docker compose up -d

# Access DB shell
docker exec -it mrp_sqlserver /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "your_sa_password" -C
```

---

**End of DEPLOY.md**