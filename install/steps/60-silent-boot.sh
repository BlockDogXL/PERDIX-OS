#!/usr/bin/env bash
# Phase 4: quiet kernel cmdline, Plymouth splash, hidden boot menu.
# See PLAN.md §4.
set -euo pipefail
source "$(dirname "$0")/../lib/common.sh"

# TODO: add "quiet splash loglevel=3 rd.udev.log_level=3 systemd.show_status=auto vt.global_cursor_default=0"
#       to the systemd-boot entries in /boot/loader/entries/*.conf
# TODO: add the plymouth hook to /etc/mkinitcpio.conf, set the theme, mkinitcpio -P
# TODO: /boot/loader/loader.conf: timeout 0, console-mode keep
# TODO: add the hidden "Maintenance" boot entry (PLAN.md §7)
warn "60-silent-boot: not implemented yet"
