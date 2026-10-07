#!/usr/bin/env bash
# Start/stop control for the desktop pet and monitor bot (systemd user services).
# Usage: ./pet_ctl.sh {start|stop|restart|status|logs} [pet|bot]
#   no target = both. Install the services once with ./install_autostart.sh
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")" || exit 1

BOT=laptop-monitor-bot.service
PET=laptop-pet.service

usage() {
    echo "Usage: $0 {start|stop|restart|status|logs} [pet|bot]"
    exit 1
}

case "${2:-both}" in
    pet)  UNITS=("$PET") ;;
    bot)  UNITS=("$BOT") ;;
    both) UNITS=("$PET" "$BOT") ;;
    *) usage ;;
esac

if ! systemctl --user list-unit-files "$BOT" --no-legend | grep -q .; then
    echo "Services not installed yet. Run: ./install_autostart.sh" >&2
    exit 1
fi

case "${1:-}" in
    start|stop|restart)
        systemctl --user "$1" "${UNITS[@]}" && echo "$1: ${UNITS[*]}"
        ;;
    status)
        for u in "${UNITS[@]}"; do
            printf '%-28s %s\n' "$u" "$(systemctl --user is-active "$u")"
        done
        ;;
    logs)
        args=()
        for u in "${UNITS[@]}"; do args+=(--user-unit "$u"); done
        journalctl "${args[@]}" -n 100 -f
        ;;
    *) usage ;;
esac
