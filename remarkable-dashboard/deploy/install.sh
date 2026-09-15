#!/usr/bin/env bash
# Stand up the daily sheet on a Linux server.
#
#   sudo ./deploy/install.sh
#
# Installs into /opt/daily-sheet with its own virtualenv, registers the systemd
# timers, and leaves .env for you to fill in. Re-running is safe: it updates the
# code and units and never touches .env.
set -euo pipefail

APP=/opt/daily-sheet
RUN_USER="${SUDO_USER:-$USER}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo $0" >&2
    exit 1
fi

say() { printf '\n== %s\n' "$1"; }

say "Timezone"
# OnCalendar follows the system timezone. On a cloud host that is UTC, so an
# 08:00 timer would fire at 10:00 Oslo in summer -- a schedule that still looks
# plausible, which is the worst kind of wrong.
current=$(timedatectl show -p Timezone --value 2>/dev/null || echo unknown)
if [[ "$current" != "Europe/Oslo" ]]; then
    timedatectl set-timezone Europe/Oslo
    echo "   set to Europe/Oslo (was $current)"
else
    echo "   already Europe/Oslo"
fi

say "Code"
mkdir -p "$APP"
# Copy rather than clone: this script runs from a checkout that already exists,
# and the server does not need git.
tar -C "$SRC" --exclude=.git --exclude=.venv --exclude=__pycache__ \
    --exclude=out --exclude=.env -cf - . | tar -C "$APP" -xf -
chown -R "$RUN_USER:$RUN_USER" "$APP"
echo "   installed to $APP"

say "Python"
if [[ ! -d "$APP/.venv" ]]; then
    python3 -m venv "$APP/.venv"
fi
"$APP/.venv/bin/pip" install --quiet --upgrade pip
"$APP/.venv/bin/pip" install --quiet -r "$APP/requirements.txt"
chown -R "$RUN_USER:$RUN_USER" "$APP/.venv"
echo "   $("$APP/.venv/bin/python" --version) with the packages"

say "Settings"
if [[ ! -f "$APP/.env" ]]; then
    cp "$APP/.env.example" "$APP/.env"
    token=$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 24)
    sed -i "s|^WEB_TOKEN=.*|WEB_TOKEN=$token|" "$APP/.env"
    sed -i "s|^RMAPI_BIN=.*|RMAPI_BIN=$APP/rmapi|" "$APP/.env"
    chown "$RUN_USER:$RUN_USER" "$APP/.env"
    chmod 600 "$APP/.env"
    echo "   created $APP/.env (WEB_TOKEN=$token)"
else
    echo "   $APP/.env left as it is"
fi

say "Services"
for unit in "$SRC"/deploy/daily-sheet-*.{service,timer}; do
    sed "s|__USER__|$RUN_USER|g" "$unit" > "/etc/systemd/system/$(basename "$unit")"
done
systemctl daemon-reload
systemctl enable --now daily-sheet-generate.timer daily-sheet-sync.timer
systemctl enable --now daily-sheet-server.service
echo "   timers enabled, server running on :8080"

cat <<NEXT

────────────────────────────────────────────────
Still needs you:

  1. Fill in $APP/.env
       ASANA_PAT, ANTHROPIC_API_KEY, CALENDAR_JSON

  2. Install and pair rmapi:
       curl -L -o /tmp/rmapi.tar.gz \\
         https://github.com/ddvk/rmapi/releases/latest/download/rmapi-linux-amd64.tar.gz
       tar -xzf /tmp/rmapi.tar.gz -C $APP rmapi
       $APP/rmapi            # paste the code from my.remarkable.com

  3. Get calendar.json onto this box, and point CALENDAR_JSON at it.

Then:
  sudo -u $RUN_USER $APP/.venv/bin/python -m daily_sheet doctor
  systemctl list-timers 'daily-sheet*'
  journalctl -u daily-sheet-generate -n 50

NEXT
