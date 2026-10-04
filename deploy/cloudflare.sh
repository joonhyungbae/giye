#!/usr/bin/env bash
# Put giye.org on Cloudflare: zone, tunnel to the VPS, DNS, HTTPS settings, edge bot and rate
# rules. Safe to rerun (every step looks up what exists first and updates it in place).
#
# Why: Cloudflare is the only way into the origin. The tunnel is dialled out from the VPS, so the
# VPS opens no web port and visitors cannot bypass the edge; Cloudflare therefore owns the
# cf-connecting-ip header the scrape guard counts by. The edge rules are a first, coarse layer
# (burst limit, known bad bots); the per-visitor enumeration limit stays in src/lib/scrape-guard.ts.
#
# Usage: deploy/cloudflare.sh   (prints the name servers to set at the registrar, Gabia)
# Needs CLOUDFLARE_API_TOKEN (Zone: Zone/DNS/Zone Settings/Zone WAF/Bot Management/Single Redirect edit, Account: Cloudflare Tunnel edit)
# in the repository .env.

source "$(dirname "$0")/lib.sh"
load_env
[[ -n "${CF_API_TOKEN:-}" && -n "${CF_ACCOUNT_ID:-}" ]] || die "CF_API_TOKEN and CF_ACCOUNT_ID must be set"
TUNNEL_NAME="giye-web"
TOKEN_FILE="$HOME/.config/giye/tunnel-token"
ORIGIN="http://127.0.0.1:3000"
# Edge burst limit: 60 requests per 10 s per visitor (the free plan's fixed period). Page loads
# fetch a few server functions each; a person clicking around stays far below it.
EDGE_REQS_PER_10S=60

# 1. Zone
zone="$(cf GET "/zones?name=$DOMAIN" | jq -r '.result[0].id // empty')"
if [[ -z "$zone" ]]; then
  zone="$(cf POST /zones "$(jq -n --arg d "$DOMAIN" --arg a "$CF_ACCOUNT_ID" '{name:$d, account:{id:$a}, type:"full"}')" | jq -r '.result.id')"
  echo "zone: created $zone"
fi
zinfo="$(cf GET "/zones/$zone")"
echo "zone status: $(jq -r '.result.status' <<<"$zinfo")"

# 2. Tunnel (remotely managed: its ingress lives in Cloudflare, the VPS only holds the token)
tid="$(cf GET "/accounts/$CF_ACCOUNT_ID/cfd_tunnel?name=$TUNNEL_NAME&is_deleted=false" | jq -r '.result[0].id // empty')"
if [[ -z "$tid" ]]; then
  tid="$(cf POST "/accounts/$CF_ACCOUNT_ID/cfd_tunnel" "$(jq -n --arg n "$TUNNEL_NAME" '{name:$n, config_src:"cloudflare"}')" | jq -r '.result.id')"
  echo "tunnel: created $tid"
fi
cf PUT "/accounts/$CF_ACCOUNT_ID/cfd_tunnel/$tid/configurations" "$(jq -n --arg h "$DOMAIN" --arg o "$ORIGIN" \
  '{config:{ingress:[{hostname:$h, service:$o}, {service:"http_status:404"}]}}')" >/dev/null
mkdir -p "$(dirname "$TOKEN_FILE")"
( umask 077; cf GET "/accounts/$CF_ACCOUNT_ID/cfd_tunnel/$tid/token" | jq -r '.result' > "$TOKEN_FILE" )
echo "tunnel: $tid, token saved to $TOKEN_FILE"

# 3. DNS: apex to the tunnel; www exists only so the redirect rule below can answer it.
upsert_cname() { # name target
  local rec body
  rec="$(cf GET "/zones/$zone/dns_records?name=$1" | jq -r '.result[] | "\(.id) \(.type)"')"
  body="$(jq -n --arg n "$1" --arg c "$2" '{type:"CNAME", name:$n, content:$c, proxied:true, ttl:1}')"
  while read -r id type; do
    [[ -z "$id" ]] && continue
    if [[ "$type" == "CNAME" ]]; then cf PUT "/zones/$zone/dns_records/$id" "$body" >/dev/null; echo "dns: updated $1"; return; fi
    cf DELETE "/zones/$zone/dns_records/$id" >/dev/null; echo "dns: removed $type $1"
  done <<<"$rec"
  cf POST "/zones/$zone/dns_records" "$body" >/dev/null; echo "dns: created $1"
}
upsert_cname "$DOMAIN" "$tid.cfargotunnel.com"
upsert_cname "www.$DOMAIN" "$DOMAIN"

# 4. HTTPS settings
for s in 'always_use_https "on"' 'min_tls_version "1.2"' 'automatic_https_rewrites "on"' 'ssl "full"'; do
  cf PATCH "/zones/$zone/settings/${s%% *}" "{\"value\":${s#* }}" >/dev/null
done
echo "settings: https only, TLS >= 1.2"

# 5. www -> apex (301), keeping path and query
# Needs Zone · Single Redirect · Edit on the token; without it the step is skipped with a warning.
( cf PUT "/zones/$zone/rulesets/phases/http_request_dynamic_redirect/entrypoint" "$(jq -n --arg d "$DOMAIN" '{rules:[{
  description:"www to apex", expression:("(http.host eq \"www." + $d + "\")"), action:"redirect",
  action_parameters:{from_value:{status_code:301, preserve_query_string:true,
    target_url:{expression:("concat(\"https://" + $d + "\", http.request.uri.path)")}}}}]}')" >/dev/null ) \
  && echo "rule: www redirect" || echo "warning: www redirect not set (token lacks Single Redirect: Edit)" >&2

# 6. Edge burst limit on everything except static build assets
cf PUT "/zones/$zone/rulesets/phases/http_ratelimit/entrypoint" "$(jq -n --argjson n "$EDGE_REQS_PER_10S" '{rules:[{
  description:"burst limit per visitor", expression:"not starts_with(http.request.uri.path, \"/assets/\")", action:"block",
  ratelimit:{characteristics:["cf.colo.id","ip.src"], period:10, requests_per_period:$n, mitigation_timeout:10}}]}')" >/dev/null
echo "rule: edge rate limit $EDGE_REQS_PER_10S / 10 s"

# 7. Bot Fight Mode: challenges known automated clients; verified search and assistant bots pass.
cf PUT "/zones/$zone/bot_management" '{"fight_mode":true,"enable_js":true}' >/dev/null
echo "bots: Bot Fight Mode on"
cf GET "/zones/$zone/bot_management" | jq '.result | {fight_mode, ai_bots_protection, crawler_protection, is_robots_txt_managed}'

echo
echo "Set these name servers for $DOMAIN at Gabia (replacing ns.gabia.*):"
jq -r '.result.name_servers[]' <<<"$zinfo"
