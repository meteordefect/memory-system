#!/bin/sh
# Render's disk is root-owned. The Drive sync process runs as the image
# user app (uid 10001) and exits if it cannot write /data.
set -eu

mkdir -p /data
gid=$(id -g app)
chown "10001:${gid}" /data

export HOME=/data
export LISTEN_ADDR=":${PORT:-8080}"
exec su-exec "10001:${gid}" /usr/local/bin/hindsight-ingestion
