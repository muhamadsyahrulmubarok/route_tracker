#!/usr/bin/env bash
# Deploy geomaps on the server: pull latest code and restart PM2.
# Usage (on server, inside the project folder):
#   ./deploy.sh
#   bash deploy.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "==> Project: $ROOT"

if [ -d .git ]; then
  echo "==> git pull"
  git pull --ff-only
else
  echo "==> No .git directory — skip pull"
fi

if [ -f requirements.txt ]; then
  if [ -x ".venv/bin/pip" ]; then
    echo "==> pip install -r requirements.txt (.venv)"
    .venv/bin/pip install -r requirements.txt -q
  elif command -v pip3 >/dev/null 2>&1; then
    echo "==> pip3 install -r requirements.txt"
    pip3 install -r requirements.txt -q
  fi
fi

mkdir -p logs

if ! command -v pm2 >/dev/null 2>&1; then
  echo "Error: pm2 not found on PATH. Install with: npm i -g pm2" >&2
  exit 1
fi

if pm2 describe geomaps >/dev/null 2>&1; then
  echo "==> pm2 restart geomaps"
  pm2 restart ecosystem.config.cjs --update-env
else
  echo "==> pm2 start ecosystem.config.cjs"
  pm2 start ecosystem.config.cjs
fi

pm2 save
echo "==> Done"
pm2 status geomaps
