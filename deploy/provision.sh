#!/usr/bin/env bash
# Prepare the VPS created by deploy/vultr.sh: system updates, Node 22, the giye user and
# directories, the giye-web service, and cloudflared. Run from the home machine; safe to rerun.
#
# Why: the origin is set up from a script so it can be rebuilt from scratch (it holds nothing
# that is not on the home machine). cloudflared is installed with the tunnel token written by
# deploy/cloudflare.sh; without the token it is installed but not started.
#
# Usage: deploy/provision.sh    (after deploy/vultr.sh; rerun after deploy/cloudflare.sh)

source "$(dirname "$0")/lib.sh"
HOST="$(remote_host)"
TOKEN_FILE="$HOME/.config/giye/tunnel-token"
TOKEN="$( [[ -f "$TOKEN_FILE" ]] && cat "$TOKEN_FILE" || true )"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new)

scp "${SSH_OPTS[@]}" "$(dirname "$0")/giye-web.service" "root@$HOST:/etc/systemd/system/giye-web.service"

ssh "${SSH_OPTS[@]}" "root@$HOST" TUNNEL_TOKEN="$TOKEN" REMOTE_USER="$REMOTE_USER" REMOTE_ROOT="$REMOTE_ROOT" bash -s <<'EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -q
apt-get upgrade -yq
apt-get install -yq ca-certificates curl gnupg rsync unattended-upgrades ufw

# Node 22 (the version the site is built and tested with)
if ! node -v 2>/dev/null | grep -q '^v22\.'; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -yq nodejs
fi

# cloudflared from Cloudflare's apt repository
if ! command -v cloudflared >/dev/null; then
  mkdir -p --mode=0755 /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
    > /etc/apt/sources.list.d/cloudflared.list
  apt-get update -q && apt-get install -yq cloudflared
fi

# Host firewall as a second layer behind the Vultr firewall group: SSH in, nothing else.
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw --force enable

# Deploy user: owns the app, may restart only its own service.
id "$REMOTE_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$REMOTE_USER"
usermod -aG systemd-journal "$REMOTE_USER"   # read giye-web logs
install -d -m 700 -o "$REMOTE_USER" -g "$REMOTE_USER" "/home/$REMOTE_USER/.ssh"
install -m 600 -o "$REMOTE_USER" -g "$REMOTE_USER" /root/.ssh/authorized_keys "/home/$REMOTE_USER/.ssh/authorized_keys"
echo "$REMOTE_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart giye-web, /usr/bin/systemctl status giye-web" \
  > /etc/sudoers.d/giye-web
chmod 440 /etc/sudoers.d/giye-web
install -d -o "$REMOTE_USER" -g "$REMOTE_USER" "$REMOTE_ROOT" "$REMOTE_ROOT/data" "$REMOTE_ROOT/data/site"
# data/work holds self-reports (personal data): owner only
install -d -m 700 -o "$REMOTE_USER" -g "$REMOTE_USER" "$REMOTE_ROOT/data/work"

# Password logins off; keys only.
sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl reload ssh

systemctl daemon-reload
systemctl enable giye-web
[[ -f "$REMOTE_ROOT/.output/server/index.mjs" ]] && systemctl restart giye-web || echo "giye-web: no build yet (run deploy/push.sh)"

if [[ -n "$TUNNEL_TOKEN" ]]; then
  if systemctl is-enabled cloudflared >/dev/null 2>&1; then
    echo "cloudflared: already installed as a service"
  else
    cloudflared service install "$TUNNEL_TOKEN"
  fi
else
  echo "cloudflared: no tunnel token yet (run deploy/cloudflare.sh, then this script again)"
fi
echo "provision: done"
EOF
