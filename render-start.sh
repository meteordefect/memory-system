#!/bin/bash
# One container: Postgres on the mounted volume, then the MCP app.
set -eu

DATA_ROOT="${PGDATA_ROOT:-/var/lib/postgresql}"
DATA_DIR="$DATA_ROOT/data"
INITDB=/usr/lib/postgresql/16/bin/initdb
PG_CTL=/usr/lib/postgresql/16/bin/pg_ctl
PSQL=/usr/lib/postgresql/16/bin/psql

if [ -z "${MCP_API_KEY:-}" ]; then
  echo "MCP_API_KEY is required." >&2
  exit 1
fi

mkdir -p "$DATA_DIR"
chown postgres:postgres "$DATA_ROOT" "$DATA_DIR"
chmod 700 "$DATA_DIR"

if [ ! -f "$DATA_DIR/PG_VERSION" ]; then
  su -s /bin/bash postgres -c "$INITDB -D '$DATA_DIR' --username=postgres --auth=trust --auth-local=trust"
fi

if ! grep -q "company-brain" "$DATA_DIR/postgresql.conf"; then
  cat >> "$DATA_DIR/postgresql.conf" <<'EOF'
# company-brain
listen_addresses = '127.0.0.1'
port = 5432
shared_buffers = 64MB
max_connections = 20
work_mem = 8MB
maintenance_work_mem = 64MB
EOF
fi

if ! grep -q "company-brain" "$DATA_DIR/pg_hba.conf"; then
  cat >> "$DATA_DIR/pg_hba.conf" <<'EOF'
# company-brain
host all all 127.0.0.1/32 trust
host all all ::1/128 trust
EOF
fi

su -s /bin/bash postgres -c "$PG_CTL -D '$DATA_DIR' -w -t 60 -l '$DATA_DIR/postgres.log' start" \
  || { echo "Postgres failed to start"; cat "$DATA_DIR/postgres.log" >&2 || true; exit 1; }

role=$(su -s /bin/bash postgres -c "$PSQL -d postgres -tAc \"SELECT 1 FROM pg_roles WHERE rolname='company_brain'\"")
if [ "$role" != "1" ]; then
  su -s /bin/bash postgres -c "$PSQL -d postgres -c \"CREATE ROLE company_brain LOGIN SUPERUSER\""
fi
db=$(su -s /bin/bash postgres -c "$PSQL -d postgres -tAc \"SELECT 1 FROM pg_database WHERE datname='company_brain'\"")
if [ "$db" != "1" ]; then
  su -s /bin/bash postgres -c "$PSQL -d postgres -c \"CREATE DATABASE company_brain OWNER company_brain\""
fi

export DATABASE_URL="${DATABASE_URL:-postgresql://company_brain@127.0.0.1:5432/company_brain}"
cd /app
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
