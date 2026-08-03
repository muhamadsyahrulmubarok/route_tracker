#!/usr/bin/env bash
# Install git hooks so `git pull` on the server restarts PM2.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_SRC="$ROOT/scripts/git-hooks/post-merge"
HOOK_DST="$ROOT/.git/hooks/post-merge"

if [ ! -d "$ROOT/.git" ]; then
  echo "Error: $ROOT is not a git repo" >&2
  exit 1
fi

cp "$HOOK_SRC" "$HOOK_DST"
chmod +x "$HOOK_DST" "$ROOT/deploy.sh" "$HOOK_SRC"
echo "Installed: $HOOK_DST"
echo "Now every successful git pull/merge on this machine will restart PM2 (geomaps)."
