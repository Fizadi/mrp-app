#!/bin/bash
set -e

DB_SERVER="${DB_SERVER:-sqlserver}"
DB_PORT="${DB_PORT:-1433}"
DB_NAME="${DB_NAME:-MRP_database}"
BACKUP_FILE="${BACKUP_FILE:-/app/backups/MRP_database.bak}"
APP_USER="${DB_USER:-mrp_user}"
APP_PASS="${DB_PASSWORD:?DB_PASSWORD is required}"
SA_PASS="${MSSQL_SA_PASSWORD:?MSSQL_SA_PASSWORD is required}"
MARKER="/app/backups/.restored_${DB_NAME}"

# Shared path inside the SQL Server container. Both containers mount the
# mssql_backup named volume here, so a file copied by this script becomes
# readable by SQL Server's RESTORE command.
SHARED_BACKUP_DIR="/var/opt/mssql/backups"
SHARED_BACKUP_FILE="${SHARED_BACKUP_DIR}/MRP_database.bak"

SQLCMD=""
for c in /opt/mssql-tools18/bin/sqlcmd /opt/mssql-tools/bin/sqlcmd; do
    [ -x "$c" ] && SQLCMD="$c" && break
done
[ -z "$SQLCMD" ] && { echo "[ERROR] sqlcmd not found"; exit 1; }

sqlcmd_sa()  { "$SQLCMD" -S "${DB_SERVER},${DB_PORT}" -U sa -P "${SA_PASS}" -C -b "$@"; }
sqlcmd_app() { "$SQLCMD" -S "${DB_SERVER},${DB_PORT}" -U "${APP_USER}" -P "${APP_PASS}" -C -b "$@"; }

echo "[..] Waiting for SQL Server at ${DB_SERVER}:${DB_PORT} ..."
for i in $(seq 1 90); do
    if sqlcmd_sa -Q "SELECT 1" -o /dev/null 2>/dev/null; then
        echo "[OK] SQL Server ready (attempt ${i})."
        break
    fi
    [ "$i" -eq 90 ] && { echo "[ERROR] SQL Server timeout."; exit 1; }
    sleep 5
done

# ---------- Restore database (first boot only) ----------
DB_EXISTS=$(sqlcmd_sa -h -1 -W \
    -Q "SET NOCOUNT ON; SELECT COUNT(*) FROM sys.databases WHERE name='${DB_NAME}';" \
    | tr -d '[:space:]')

if [ "${DB_EXISTS}" != "1" ]; then
    if [ ! -f "${BACKUP_FILE}" ]; then
        echo "[WARN] ${DB_NAME} missing and ${BACKUP_FILE} not found — skipping restore."
    else
        echo "[..] Restoring ${DB_NAME} from ${BACKUP_FILE} ..."

        # ---- Copy the .bak into the shared volume so SQL Server can read it ----
        # SQL Server's RESTORE runs inside the sqlserver container and can only
        # see paths that exist on *its* filesystem. The bind-mount of ./backups
        # into /app/backups is only visible to the web container. The
        # mssql_backup named volume is mounted in both containers at
        # /var/opt/mssql/backups, so we stage the .bak there first.
        echo "[..] Staging backup into shared volume ${SHARED_BACKUP_DIR} ..."
        mkdir -p "${SHARED_BACKUP_DIR}" 2>/dev/null || true
        if [ ! -f "${SHARED_BACKUP_FILE}" ]; then
            cp "${BACKUP_FILE}" "${SHARED_BACKUP_FILE}"
        fi
        echo "[OK] Copied $(stat -c%s "${SHARED_BACKUP_FILE}") bytes to ${SHARED_BACKUP_FILE}"

        DATA_DIR=$(sqlcmd_sa -h -1 -W -Q "SET NOCOUNT ON; SELECT SERVERPROPERTY('InstanceDefaultDataPath');" | tr -d '[:space:]')
        LOG_DIR=$(sqlcmd_sa  -h -1 -W -Q "SET NOCOUNT ON; SELECT SERVERPROPERTY('InstanceDefaultLogPath');"  | tr -d '[:space:]')

        echo "[..] Target data dir: ${DATA_DIR}"
        echo "[..] Target log  dir: ${LOG_DIR}"

        # --- Robust logical-name extraction ---
        # Query against the shared path, which SQL Server can now see.
        FILELIST=$(sqlcmd_sa -h -1 -W -s "|" -Q "SET NOCOUNT ON; RESTORE FILELISTONLY FROM DISK=N'${SHARED_BACKUP_FILE}';")

        echo "[..] FILELISTONLY raw output:"
        echo "${FILELIST}" | sed 's/^/     /'

        DATA_LOGICAL=$(echo "${FILELIST}" | awk -F'|' 'NF>=2 && $1!="" && $1!~/^-/ {print $1; exit}')
        LOG_LOGICAL=$(echo  "${FILELIST}" | awk -F'|' 'NF>=2 && $1!="" && $1!~/^-/ {print $1}' | tail -n 1)

        if [ -z "${DATA_LOGICAL}" ] || [ -z "${LOG_LOGICAL}" ]; then
            echo "[ERROR] Could not parse logical names from .bak."
            echo "[ERROR] DATA_LOGICAL='${DATA_LOGICAL}'  LOG_LOGICAL='${LOG_LOGICAL}'"
            exit 1
        fi

        echo "[..] Data logical name: ${DATA_LOGICAL}"
        echo "[..] Log  logical name: ${LOG_LOGICAL}"

        # Run the restore from the shared path.
        RESTORE_OUTPUT=$(sqlcmd_sa -Q "
            RESTORE DATABASE [${DB_NAME}]
            FROM DISK = N'${SHARED_BACKUP_FILE}'
            WITH
                MOVE N'${DATA_LOGICAL}' TO N'${DATA_DIR%/}/${DB_NAME}.mdf',
                MOVE N'${LOG_LOGICAL}'  TO N'${LOG_DIR%/}/${DB_NAME}_log.ldf',
                RECOVERY, REPLACE, STATS = 10;
        " 2>&1) || {
            echo "[ERROR] Restore failed. Output:"
            echo "${RESTORE_OUTPUT}" | sed 's/^/     /'
            exit 1
        }

        echo "[..] Restore output:"
        echo "${RESTORE_OUTPUT}" | sed 's/^/     /'

        # Post-verify: database must now exist and be online.
        POST_CHECK=$(sqlcmd_sa -h -1 -W \
            -Q "SET NOCOUNT ON; SELECT COUNT(*) FROM sys.databases WHERE name='${DB_NAME}' AND state=0;" \
            | tr -d '[:space:]')

        if [ "${POST_CHECK}" != "1" ]; then
            echo "[ERROR] Restore reported success but ${DB_NAME} is not online."
            exit 1
        fi

        echo "[OK] Restore complete — ${DB_NAME} is online."
    fi
else
    echo "[--] ${DB_NAME} already exists — skipping restore."
fi

# ---------- Ensure app login/user exists (idempotent, every boot) ----------
# This block runs on every container start, regardless of whether the
# database was just restored. It is safe to re-run: the IF NOT EXISTS
# guards make it idempotent. Running it unconditionally means the app
# login is recreated even on subsequent boots when the DB already exists
# in the persistent mssql_data volume.
echo "[..] Ensuring login [${APP_USER}] ..."
sqlcmd_sa -Q "
    IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name='${APP_USER}')
        CREATE LOGIN [${APP_USER}] WITH PASSWORD=N'${APP_PASS}', CHECK_POLICY=OFF;
    ALTER LOGIN [${APP_USER}] ENABLE;
    GRANT CONNECT SQL TO [${APP_USER}];
"
sqlcmd_sa -d "${DB_NAME}" -Q "
    IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name='${APP_USER}')
        CREATE USER [${APP_USER}] FOR LOGIN [${APP_USER}];
    ALTER ROLE db_owner ADD MEMBER [${APP_USER}];
"
echo "[OK] Login/user ready."

touch "${MARKER}" 2>/dev/null || true

echo "[>>] Starting: $@"
exec "$@"