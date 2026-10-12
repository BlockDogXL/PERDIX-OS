#!/usr/bin/env bash
# Phase 2: Steam, gamemode, MangoHud and gaming sysctls.
# See PLAN.md §4.
set -euo pipefail
source "$(dirname "$0")/../lib/common.sh"

# TODO: pacman_install_list gaming
# TODO: install_rootfs_file etc/sysctl.d/80-kiosk.conf && sysctl --system
warn "30-steam: not implemented yet"
