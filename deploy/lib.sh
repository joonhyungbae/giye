# Shared settings for the deploy scripts. Sourced, not run.
# Secrets live in the git-ignored .env at the repository root (mode 600):
#   VULTR_API_KEY, CLOUDFLARE_API_TOKEN (or CF_API_TOKEN), optional CLOUDFLARE_ACCOUNT_ID
#   (looked up from the token when absent)
# The VPS address is written to ~/.config/giye/host by deploy/vultr.sh.

set -euo pipefail

GIYE_ENV="${GIYE_ENV:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env}"
GIYE_HOST_FILE="$HOME/.config/giye/host"
DOMAIN="giye.org"
REMOTE_USER="giye"
REMOTE_ROOT="/srv/giye"

die() { echo "error: $*" >&2; exit 1; }

load_env() {
  [[ -f "$GIYE_ENV" ]] || die "missing $GIYE_ENV (see docs/DEPLOY.md)"
  # shellcheck disable=SC1090
  set -a; source "$GIYE_ENV"; set +a
  CF_API_TOKEN="${CF_API_TOKEN:-${CLOUDFLARE_API_TOKEN:-}}"
  CF_ACCOUNT_ID="${CF_ACCOUNT_ID:-${CLOUDFLARE_ACCOUNT_ID:-}}"
  if [[ -n "$CF_API_TOKEN" && -z "$CF_ACCOUNT_ID" ]]; then
    # /accounts needs Account Settings: Read; a zone-scoped token still sees its zones' account.
    CF_ACCOUNT_ID="$(cf GET /accounts | jq -r '.result[0].id // empty')"
    [[ -n "$CF_ACCOUNT_ID" ]] || CF_ACCOUNT_ID="$(cf GET /zones | jq -r '.result[0].account.id // empty')"
  fi
  [[ -z "$CF_API_TOKEN" || -n "$CF_ACCOUNT_ID" ]] || die "set CLOUDFLARE_ACCOUNT_ID in $GIYE_ENV"
}

remote_host() {
  [[ -f "$GIYE_HOST_FILE" ]] || die "missing $GIYE_HOST_FILE; run deploy/vultr.sh first"
  cat "$GIYE_HOST_FILE"
}

# curl wrappers that fail loudly on API errors
vultr() { # vultr METHOD PATH [JSON]
  # Retries: the Vultr API answers 502/504 under load. POST /instances is not retried (it could
  # create a second instance); vultr.sh looks the instance up by label before creating one.
  local retry=(--retry 4 --retry-all-errors --retry-delay 5)
  [[ "$1" == "POST" ]] && retry=()
  curl -sS --fail-with-body --max-time 90 "${retry[@]}" -X "$1" "https://api.vultr.com/v2$2" \
    -H "Authorization: Bearer $VULTR_API_KEY" -H "Content-Type: application/json" ${3:+--data "$3"}
}
cf() { # cf METHOD PATH [JSON]
  local out
  out="$(curl -sS -X "$1" "https://api.cloudflare.com/client/v4$2" \
    -H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json" ${3:+--data "$3"})"
  if [[ "$(jq -r '.success' <<<"$out")" != "true" ]]; then
    echo "$out" | jq '.errors' >&2
    die "Cloudflare API $1 $2 failed"
  fi
  echo "$out"
}
