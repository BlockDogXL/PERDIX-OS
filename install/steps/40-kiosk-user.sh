#!/usr/bin/env bash
# Phase 3: create the unprivileged kiosk user and its dotfiles.
# See PLAN.md §4.
set -euo pipefail
source "$(dirname "$0")/../lib/common.sh"

# TODO: useradd -m -G video,audio,input "$KIOSK_USER" (skip if it exists); passwd -l
# TODO: copy skel/.bash_profile, skel/.xinitrc to the home dir, chown, touch .hushlogin
# TODO: create the "admin" maintenance account with sudo (PLAN.md §7)
warn "40-kiosk-user: not implemented yet"
