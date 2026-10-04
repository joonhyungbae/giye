#!/usr/bin/env bash
# Move self-reports from the VPS to the home machine and delete them there.
#
# Why: /request writes personal data (names, contact addresses) to data/work/requests.jsonl on
# the VPS. It should sit on the server only until the next pull. The server file is renamed
# first, so a report that arrives during the copy goes to a fresh file and is not lost. Rows are
# appended to the local data/work/requests.jsonl that scripts/import_requests.py reads, and each
# pulled file is also kept as data/work/requests-remote/<name> (original bytes).
#
# Usage: deploy/pull_requests.sh   (run before scripts/import_requests.py; cron-friendly)

source "$(dirname "$0")/lib.sh"
cd "$(dirname "$0")/.."
HOST="$(remote_host)"
KEEP=data/work/requests-remote
mkdir -p "$KEEP"; chmod 700 data/work "$KEEP"

ssh "$REMOTE_USER@$HOST" "cd $REMOTE_ROOT/data/work && if [ -s requests.jsonl ]; then mv requests.jsonl requests.\$(date -u +%Y%m%dT%H%M%SZ).jsonl; fi"
rsync -a --remove-source-files --include='requests.*.jsonl' --exclude='*' \
  "$REMOTE_USER@$HOST:$REMOTE_ROOT/data/work/" "$KEEP/.incoming/"
shopt -s nullglob
n=0
for f in "$KEEP"/.incoming/requests.*.jsonl; do
  cat "$f" >> data/work/requests.jsonl
  n=$((n + $(wc -l < "$f")))
  mv "$f" "$KEEP/"
done
echo "pulled $n request(s)"
