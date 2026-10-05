#!/usr/bin/env bash
# Start the Giye website (web/, TanStack Start dev server) against this checkout's data snapshot.
#
# Why: the site reads the file snapshot in data/site; the dev server runs inside web/, so the data
# directories are passed explicitly instead of relying on the working directory.
#
# Usage: ./web.sh             serve the current data/site at http://localhost:8080
#        ./web.sh --rebuild   rebuild data/site from the ledger first (scripts/, private checkout only)
#        PORT=3001 HOST=127.0.0.1 ./web.sh

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB="$ROOT/web"

command -v bun >/dev/null 2>&1 || { echo "error: bun is required but not found in PATH" >&2; exit 1; }

if [[ "${1:-}" == "--rebuild" ]]; then
  [[ -f "$ROOT/scripts/build_site_dataset.py" ]] || { echo "error: --rebuild needs the private scripts/" >&2; exit 1; }
  (cd "$ROOT" && python3 scripts/preprocess/run.py && python3 scripts/build_site_dataset.py)
fi
[[ -f "$ROOT/data/site/artists.json" ]] || echo "warning: $ROOT/data/site/artists.json is missing; the site will be empty" >&2

cd "$WEB"
[[ -d node_modules ]] || bun install --frozen-lockfile

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"

# A dev server left running from this checkout holds the port and inotify slots.
if pids="$(pgrep -f "$WEB/node_modules/.bin/vite dev")"; then
  echo "Stopping previous dev server(s): $(echo $pids)"
  kill $pids 2>/dev/null || true
  for _ in $(seq 10); do pgrep -f "$WEB/node_modules/.bin/vite dev" >/dev/null || break; sleep 0.5; done
fi

export GIYE_SITE_DIR="${GIYE_SITE_DIR:-$ROOT/data/site}"
export GIYE_WORK_DIR="${GIYE_WORK_DIR:-$ROOT/data/work}"
echo "Giye web: http://localhost:$PORT  (data: $GIYE_SITE_DIR)"
# Build-time site values (origin, contact) of this deployment.
[[ -f "$ROOT/deploy/site.env" ]] && { set -a; source "$ROOT/deploy/site.env"; set +a; }
exec bun run dev --host "$HOST" --port "$PORT"
