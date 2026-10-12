#!/usr/bin/env bash
# Phase 6: close escape routes (VT switch, zap, SysRq) and limit privileges.
# See PLAN.md §4.
set -euo pipefail
source "$(dirname "$0")/../lib/common.sh"

# TODO: install_rootfs_file etc/X11/xorg.conf.d/10-kiosk.conf
# TODO: install_rootfs_file etc/polkit-1/rules.d/50-kiosk-power.rules
# TODO: openbox rc.xml without menus/keybindings for the kiosk user
warn "80-lockdown: not implemented yet"
