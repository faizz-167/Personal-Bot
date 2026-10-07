#!/usr/bin/env bash
# Installs the bot and the desktop pet as systemd user services, started
# with your desktop session and restarted automatically if they crash.
# Logs go to the journal:  ./pet_ctl.sh logs
# Run as your normal user, not with sudo. Pass --remove to undo.
set -euo pipefail
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
OLD_AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/laptop-monitor.desktop"
BOT=laptop-monitor-bot.service
PET=laptop-pet.service

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as your normal user, not root." >&2
    exit 1
fi

if [ "${1:-}" = "--remove" ]; then
    systemctl --user disable --now "$BOT" "$PET" 2>/dev/null || true
    rm -f "$UNITS/$BOT" "$UNITS/$PET"
    systemctl --user daemon-reload
    echo "Removed $BOT and $PET"
    exit 0
fi

[ -f "$DIR/config.py" ] || { echo "config.py missing - copy config.example.py and fill it in first." >&2; exit 1; }
PY=/usr/bin/python3
[ -x "$DIR/.venv/bin/python" ] && PY="$DIR/.venv/bin/python"

# The token lives in config.py; keep it private.
chmod 600 "$DIR/config.py"

# Stop anything started the old way (autostart entry + nohup loop), so the
# two don't fight over the Telegram connection.
if [ -f "$OLD_AUTOSTART" ]; then
    rm -f "$OLD_AUTOSTART"
    echo "Removed old autostart entry $OLD_AUTOSTART"
fi
pkill -f "while true; do .* monitor_bot.py" 2>/dev/null || true
pkill -f "$DIR/monitor_bot.py|python -u monitor_bot.py" 2>/dev/null || true
pkill -f "python .*(hanging_pet|desktop_pet)\.py" 2>/dev/null || true
rm -f "$DIR/.pet.pid" "$DIR/.bot.pid"

mkdir -p "$UNITS"

cat > "$UNITS/$BOT" <<UNIT
[Unit]
Description=Laptop Monitor Telegram bot
PartOf=graphical-session.target
After=graphical-session.target
StartLimitIntervalSec=0

[Service]
WorkingDirectory=$DIR
ExecStart=$PY -u $DIR/monitor_bot.py
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=15
# 78 = bad config.py; restarting will not fix that.
RestartPreventExitStatus=78
TimeoutStopSec=15

[Install]
WantedBy=graphical-session.target
UNIT

cat > "$UNITS/$PET" <<UNIT
[Unit]
Description=Laptop Monitor desktop pet
PartOf=graphical-session.target
After=graphical-session.target

[Service]
WorkingDirectory=$DIR
ExecStart=$PY $DIR/hanging_pet.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now "$BOT" "$PET"
echo "Installed and started $BOT and $PET."
echo "Control them with: $DIR/pet_ctl.sh {start|stop|restart|status|logs} [pet|bot]"
