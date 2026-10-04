#!/usr/bin/env bash
# Create the giye.org origin server on Vultr (Seoul), once.
#
# Why: the site must stay up at a stable URL while the archive pipeline keeps running on the
# home machine. The VPS holds only the built server and the published snapshot (data/site),
# never the ledger or evidence. It accepts no web traffic directly: Cloudflare Tunnel connects
# out from it (deploy/cloudflare.sh), so the only open inbound port is SSH from SSH_FROM.
#
# Idempotent: reuses the SSH key, firewall group and instance labelled giye-web if they exist.
# Usage: deploy/vultr.sh        (then deploy/provision.sh)
# Env:   SSH_FROM=<cidr> to allow SSH from another address (default: this machine's public IPv4)
#        PLAN=vc2-1c-2gb OS_ID=<id> to skip the plan and OS catalogue lookups

source "$(dirname "$0")/lib.sh"
load_env
[[ -n "${VULTR_API_KEY:-}" ]] || die "VULTR_API_KEY is not set"

REGION="icn"          # Seoul: same country as most visitors and the home machine
MIN_RAM_MB=2048       # the server keeps the parsed snapshot (~41 MB JSON) in memory
LABEL="giye-web"
SSH_PUB="${SSH_PUB:-$HOME/.ssh/id_rsa.pub}"
SSH_FROM="${SSH_FROM:-$(curl -s https://api.ipify.org)/32}"

# SSH key
key_id="$(vultr GET "/ssh-keys?per_page=500" | jq -r --arg n "$LABEL" '.ssh_keys[] | select(.name==$n) | .id' | head -1)"
if [[ -z "$key_id" ]]; then
  key_id="$(vultr POST /ssh-keys "$(jq -n --arg n "$LABEL" --arg k "$(cat "$SSH_PUB")" '{name:$n, ssh_key:$k}')" | jq -r '.ssh_key.id')"
  echo "ssh key: created $key_id"
fi

# Firewall group: SSH from SSH_FROM only. No 80/443: web traffic arrives through the tunnel.
fw_id="$(vultr GET "/firewalls?per_page=500" | jq -r --arg n "$LABEL" '.firewall_groups[] | select(.description==$n) | .id' | head -1)"
if [[ -z "$fw_id" ]]; then
  fw_id="$(vultr POST /firewalls "$(jq -n --arg n "$LABEL" '{description:$n}')" | jq -r '.firewall_group.id')"
  echo "firewall group: created $fw_id"
fi
if ! vultr GET "/firewalls/$fw_id/rules?per_page=500" | jq -e --arg s "${SSH_FROM%/*}" '.firewall_rules[] | select(.port=="22" and .subnet==$s)' >/dev/null; then
  vultr POST "/firewalls/$fw_id/rules" "$(jq -n --arg s "${SSH_FROM%/*}" --argjson m "${SSH_FROM#*/}" \
    '{ip_type:"v4", protocol:"tcp", port:"22", subnet:$s, subnet_size:$m, notes:"ssh"}')" >/dev/null
  echo "firewall: ssh allowed from $SSH_FROM"
fi

# Instance
inst="$(vultr GET "/instances?label=$LABEL" | jq -r '.instances[0].id // empty')"
if [[ -z "$inst" ]]; then
  # PLAN / OS_ID skip the catalogue lookups (large responses that time out when the API is slow).
  plan="${PLAN:-}"; os="${OS_ID:-}"
  [[ -n "$plan" ]] || plan="$(vultr GET "/plans?type=vc2&per_page=500" | jq -r --arg r "$REGION" --argjson m "$MIN_RAM_MB" \
    '[.plans[] | select(.ram >= $m and (.locations | index($r)))] | sort_by(.monthly_cost) | .[0].id')"
  [[ -n "$os" ]] || os="$(vultr GET "/os?per_page=500" | jq -r '.os[] | select(.name=="Ubuntu 24.04 LTS x64") | .id')"
  [[ -n "$plan" && -n "$os" ]] || die "no plan/os found (plan=$plan os=$os)"
  inst="$(vultr POST /instances "$(jq -n --arg r "$REGION" --arg p "$plan" --argjson o "$os" --arg l "$LABEL" \
    --arg k "$key_id" --arg f "$fw_id" \
    '{region:$r, plan:$p, os_id:$o, label:$l, hostname:$l, sshkey_id:[$k], firewall_group_id:$f, backups:"disabled", enable_ipv6:true}')" \
    | jq -r '.instance.id')"
  echo "instance: created $inst (plan $plan)"
fi

echo -n "waiting for the instance"
for _ in $(seq 1 90); do
  info="$(vultr GET "/instances/$inst")"
  if [[ "$(jq -r '.instance.status' <<<"$info")" == "active" && "$(jq -r '.instance.main_ip' <<<"$info")" != "0.0.0.0" ]]; then
    break
  fi
  echo -n "."; sleep 10
done
echo
ip="$(jq -r '.instance.main_ip' <<<"$info")"
[[ "$(jq -r '.instance.status' <<<"$info")" == "active" && "$ip" != "0.0.0.0" ]] \
  || die "instance $inst is still $(jq -r '.instance.status' <<<"$info") after 15 min; check the Vultr dashboard, then run again"
mkdir -p "$(dirname "$GIYE_HOST_FILE")"
echo "$ip" > "$GIYE_HOST_FILE"
echo "host: $ip (saved to $GIYE_HOST_FILE)"
