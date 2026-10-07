#!/usr/bin/env bash
# Removes the anti-theft failed-login service. Run with sudo.
set -eu
if [ "$(id -u)" -ne 0 ]; then
    echo "Run this as root:  sudo $0" >&2
    exit 1
fi
UNIT=laptop-monitor-sentinel.service
systemctl disable --now "$UNIT" 2>/dev/null || true
rm -f "/etc/systemd/system/$UNIT"
systemctl daemon-reload
echo "Removed the failed-login service."
