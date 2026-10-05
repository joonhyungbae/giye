#!/usr/bin/env bash
# Off-machine copy of the data that cannot be rebuilt, on the giye.org VPS.
#
# Why: the ledger is versioned in the private repository, but the original bytes of cited pages
# (data/raw) are not, and a page that changes or disappears cannot be fetched again. The author's
# audit labels, CV extractions and review material are small and hard to redo. Derived folders
# (data/processed, data/site, regression runs, cv caches) are left out: the pipeline rebuilds them.
#
# Where: /home/giye/backup/ on the VPS, mode 700, outside /srv/giye, so the web service never
# serves it. No --delete: evidence is append-only, and a file removed here by mistake must not
# vanish from the copy too.
#
# Usage: deploy/backup.sh            (also run by deploy/scheduled.sh after each weekly run)

set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/lib.sh
HOST="$(remote_host)"
DEST="/home/$REMOTE_USER/backup"

PATHS=(
  data/ledger
  data/raw
  data/reference
  data/work/audit
  data/work/cv_extract
  data/work/cv_diffs
  data/work/quarantine
)

ssh "$REMOTE_USER@$HOST" "mkdir -p '$DEST' && chmod 700 '$DEST'"
for p in "${PATHS[@]}"; do
  [[ -e "$p" ]] || continue
  # -R keeps the path below the repository root, so data/raw lands at backup/data/raw.
  rsync -azR --exclude='.ledger.lock' --exclude='__pycache__/' "./$p/" "$REMOTE_USER@$HOST:$DEST/"
done
ssh "$REMOTE_USER@$HOST" "du -sh '$DEST' | sed 's/^/backup: /'; date -Iseconds > '$DEST/LAST_BACKUP'"
