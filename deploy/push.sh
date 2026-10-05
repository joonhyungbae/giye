#!/usr/bin/env bash
# Publish the site to the VPS: build the server, copy it and the published snapshot, restart.
#
# Why: the home machine stays the only place with the ledger and evidence; the VPS receives
# just .output/ (the built server) and data/site/ (what the pages show). --data-only skips the
# build and the restart: the server re-reads data/site/ when the files' modification times change.
#
# Usage: deploy/push.sh [--data-only]
# Before a data push, refresh the snapshot: python3 scripts/preprocess/run.py && python3 scripts/build_site_dataset.py

source "$(dirname "$0")/lib.sh"
cd "$(dirname "$0")/.."
HOST="$(remote_host)"
DEST="$REMOTE_USER@$HOST:$REMOTE_ROOT"
DATA_ONLY=0
[[ "${1:-}" == "--data-only" ]] && DATA_ONLY=1

[[ -f data/site/artists.json ]] || die "data/site/ has no snapshot"
# The site is built from the public package's web/ (giye.org runs the released code).
WEB_DIR="${WEB_DIR:-web}"
OUT="$WEB_DIR/.output"

# Browser check of the built home before it ships. Type checks and the build passed while the
# home threw in the browser once (a split route loader lost a module variable), so load it in
# headless Chrome against the local build and refuse to deploy on a console error or no canvas.
smoke_test() {
  local port=3199 dom log pid
  dom="$(mktemp)"; log="$(mktemp)"
  GIYE_SITE_DIR="$PWD/data/site" PORT=$port HOST=127.0.0.1 node "$OUT/server/index.mjs" >/dev/null 2>&1 & pid=$!
  sleep 3
  timeout 90 google-chrome --headless=new --disable-gpu --no-sandbox \
    --user-agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36" \
    --enable-logging=stderr --v=0 --virtual-time-budget=20000 --dump-dom "http://127.0.0.1:$port/" >"$dom" 2>"$log" || true
  kill "$pid" 2>/dev/null || true
  if grep -qiE "ReferenceError|TypeError|SyntaxError|Uncaught" "$log" || ! grep -q "<canvas" "$dom"; then
    grep -iE "ReferenceError|TypeError|SyntaxError|Uncaught" "$log" | head -5 >&2
    rm -f "$dom" "$log"
    die "smoke test failed: the built home did not render in headless Chrome"
  fi
  rm -f "$dom" "$log"
  echo "smoke test: home renders"
}

if (( ! DATA_ONLY )); then
  # The site origin and contact address are build-time values (deploy/site.env).
  (set -a; source deploy/site.env; set +a; cd "$WEB_DIR" && bun install --frozen-lockfile >/dev/null && bunx tsc --noEmit -p . && bun run build)
  smoke_test
  rsync -az --delete "$OUT/" "$DEST/.output/"
fi
# Only the published snapshot; never data/ledger, data/work or evidence.
rsync -az --delete data/site/ "$DEST/data/site/"

if (( ! DATA_ONLY )); then
  ssh "$REMOTE_USER@$HOST" sudo /usr/bin/systemctl restart giye-web
fi
sleep 2
ssh "$REMOTE_USER@$HOST" "curl -s -o /dev/null -w 'origin: HTTP %{http_code}\n' -A 'Mozilla/5.0 giye-deploy-check' http://127.0.0.1:3000/"
# Warm the home payload: the first call after a restart or a data change builds it (~2 s on this
# VPS); without this the first visitor waits for it. The id is read from the built resolver.
fn="$(grep -B1 'functionName: "getStudyData_createServerFn_handler"' "$OUT"/server/__23tanstack-start-server-fn-resolver-*.mjs \
  | grep -oE '[0-9a-f]{64}' | head -1)"
if [[ -n "$fn" ]]; then
  ssh "$REMOTE_USER@$HOST" "curl -s -o /dev/null -w 'home payload: HTTP %{http_code} in %{time_total}s\n' -A 'Mozilla/5.0 giye-deploy-check' -H 'Sec-Fetch-Site: same-origin' -H 'x-tsr-serverFn: true' http://127.0.0.1:3000/_serverFn/$fn"
fi
