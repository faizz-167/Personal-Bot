#!/usr/bin/env bash
# Linux counterpart of run.bat. The bot and pet run as systemd user services;
# install them once with ./install_autostart.sh.
exec "$(dirname "$(readlink -f "$0")")/pet_ctl.sh" start
