#!/usr/bin/env bash
# Scheduled refresh of giye.org: run the pipeline on the home machine, then publish the snapshot.
#
# Why: the frame registry page and the paper state a weekly cadence (link checks, evidence capture,
# changed CVs, self-reports). Cron runs this script so that the cadence is real. New editions are
# collected by hand when published (docs/DEPLOY.md), not here. One flock serialises runs, so a
# scheduled run and a manual one never write the ledger at the same time. The site is pushed only
# when the pipeline exits cleanly.
#
# Usage: deploy/scheduled.sh weekly     (crontab line in docs/DEPLOY.md)

set -euo pipefail
MODE="${1:?usage: deploy/scheduled.sh weekly}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p data/work/logs
LOG="data/work/logs/scheduled-${MODE}-$(date +%Y%m%d-%H%M).log"

exec 9>"data/work/.pipeline.lock"
if ! flock -n 9; then
  echo "$(date -Iseconds) another pipeline run holds the lock; skipped" >> data/work/logs/scheduled-skipped.log
  exit 0
fi

{
  echo "# scheduled $MODE run $(date -Iseconds)"
  # Back up the ledger before the run (AGENTS.md: copy before writing).
  backup="data/work/backups/ledger-$(date +%Y%m%d-%H%M)-before-scheduled-${MODE}"
  mkdir -p "$backup" && cp data/ledger/*.csv "$backup/"
  ./scripts/pipeline.sh "$MODE"
  deploy/push.sh --data-only
  # Off-machine copy of the ledger: the private repository versions data/ledger/*.csv (AGENTS.md,
  # two git directories). The public checkout has no ./gitp, so this step is skipped there.
  if [[ -x ./gitp ]]; then
    ./gitp add -f data/ledger/*.csv
    ./gitp diff --cached --quiet -- data/ledger || ./gitp commit -q -m "Ledger after the scheduled $MODE run ($(date +%F))"
    ./gitp push -q || echo "ledger push failed; the commit stays local"
  fi
  echo "# done $(date -Iseconds)"
} >> "$LOG" 2>&1
