#!/usr/bin/env sh
set -eu

export APP_HOST="${APP_HOST:-0.0.0.0}"
export APP_PORT="${APP_PORT:-5001}"
export APP_DATA_DIR="${APP_DATA_DIR:-/app/data}"

mkdir -p "$APP_DATA_DIR"

echo "Starting Workbook Compare"
echo "APP_HOST=$APP_HOST"
echo "APP_PORT=$APP_PORT"
echo "APP_DATA_DIR=$APP_DATA_DIR"

exec python app.py
