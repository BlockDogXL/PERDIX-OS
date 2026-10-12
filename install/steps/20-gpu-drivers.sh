#!/usr/bin/env bash
# Phase 2: detect GPU vendor(s) and install Mesa/Vulkan or NVIDIA drivers.
# See PLAN.md §4.
set -euo pipefail
source "$(dirname "$0")/../lib/common.sh"

# TODO: lspci -nn | grep -E "VGA|3D|Display" -> amd / intel / nvidia (hybrid laptops: install both)
# TODO: pacman_install_list gpu-<vendor>
# TODO: NVIDIA: add nvidia_drm.modeset=1 to the kernel cmdline, rebuild initramfs
warn "20-gpu-drivers: not implemented yet"
