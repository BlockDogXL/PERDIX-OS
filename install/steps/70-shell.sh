#!/usr/bin/env bash
# Phase 5: install the PyQt5 desktop shell and its session wrapper.
# See PLAN.md §4.
set -euo pipefail
source "$(dirname "$0")/../lib/common.sh"

# TODO: pacman_install_list shell
# TODO: rsync shell/kiosk_shell to /opt/kiosk-shell/
# TODO: install_rootfs_file usr/local/bin/kiosk-session 0755
# TODO: install_rootfs_file etc/kiosk/kiosk.conf
# TODO: write /etc/perdix-release (VERSION=..., EDITION="Installed") for the About System page
# TODO: systemctl enable clamav-freshclam.service (virus definitions for the Antivirus Scan page)
warn "70-shell: not implemented yet"
