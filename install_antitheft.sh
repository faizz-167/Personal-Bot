#!/usr/bin/env bash
# Installs the anti-theft failed-login alert on Linux.
#
# What it does (one-time, needs root):
#   1. Registers a systemd service that runs antitheft_sentinel.py as root.
#      It starts at boot, before anyone logs in, so it is armed at the login
#      screen -- the whole point.
#   2. Sends a test message, so you know Telegram delivery works.
#   3. Shows any refused logins already in the journal from the last 24h.
#
# Run it with:  sudo ./install_antitheft.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "This must run as root:  sudo $0" >&2
    exit 1
fi

UNIT=laptop-monitor-sentinel.service
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SCRIPT="$DIR/antitheft_sentinel.py"
PY=/usr/bin/python3
[ -x "$DIR/.venv/bin/python" ] && PY="$DIR/.venv/bin/python"

[ -f "$SCRIPT" ] || { echo "sentinel not found at $SCRIPT" >&2; exit 1; }
[ -f "$DIR/config.py" ] || { echo "config.py missing - copy config.example.py first" >&2; exit 1; }

echo "[1/3] Registering systemd service $UNIT ..."
cat > "/etc/systemd/system/$UNIT" <<SERVICE
[Unit]
Description=Anti-theft: alert on Telegram when a login fails
Wants=network-online.target
After=network-online.target systemd-journald.service

[Service]
ExecStart=$PY $SCRIPT watch
WorkingDirectory=$DIR
# Root must not leave root-owned __pycache__ files in your project folder.
Environment=PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE
systemctl daemon-reload
systemctl enable --now "$UNIT"
systemctl --no-pager --lines=0 status "$UNIT" || true

echo
echo "[2/3] Sending a test message ..."
"$PY" "$SCRIPT" test

echo
echo "[3/3] Refused logins already in the journal (last 24h):"
journalctl --no-pager -q --since=-24h SYSLOG_FACILITY=4 SYSLOG_FACILITY=10 \
    | grep -E 'pam_[a-z_]+\([^)]*:auth\): authentication failure' | tail -n 10 \
    || echo "  none in the last 24h (that's fine)."

echo
echo "Done."
echo "TEST IT: lock the screen (Super+L), type a WRONG password once, then"
echo "log in properly. You should get a Telegram alert within a few seconds."
echo "Logs:  journalctl -u $UNIT"
