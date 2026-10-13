#!/usr/bin/env bash
# Phase 1: enable multilib, upgrade, install base packages.
# See PLAN.md §4.
set -euo pipefail
source "$(dirname "$0")/../lib/common.sh"

# TODO: uncomment [multilib] in /etc/pacman.conf (idempotently)
# TODO: pacman -Syu --noconfirm
# TODO: pacman_install_list base; pacman_install_list admin
# TODO: systemctl enable NetworkManager
warn "10-base: not implemented yet"
