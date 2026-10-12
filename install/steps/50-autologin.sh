#!/usr/bin/env bash
# Phase 3: autologin the kiosk user on tty1.
# See PLAN.md §4.
set -euo pipefail
source "$(dirname "$0")/../lib/common.sh"

# TODO: install_rootfs_file etc/systemd/system/getty@tty1.service.d/autologin.conf
# TODO: systemctl daemon-reload
warn "50-autologin: not implemented yet"
