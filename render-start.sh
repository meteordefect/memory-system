#!/bin/bash
# Render disk mount is root-owned. Hindsight's embedded Postgres runs as
# UID 1000 and exits if it cannot write $HOME/.pg0.
set -eu

DATA_DIR="/home/hindsight/.pg0"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR"
  chown hindsight:hindsight "$DATA_DIR"
  if ! command -v setpriv >/dev/null 2>&1; then
    echo "setpriv is missing; cannot drop to the hindsight user." >&2
    exit 1
  fi
  export HOME=/home/hindsight
  export USER=hindsight
  export LOGNAME=hindsight
  cd /app
  exec setpriv --reuid=hindsight --regid=hindsight --init-groups --inh-caps=-all /app/start-all.sh
fi

if [ ! -w "$DATA_DIR" ]; then
  echo "Disk $DATA_DIR is not writable by UID $(id -u)." >&2
  exit 1
fi

exec /app/start-all.sh
