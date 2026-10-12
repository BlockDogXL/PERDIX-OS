#!/usr/bin/env bash
# Helpers shared by install.sh and every step script.

KIOSK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
KIOSK_USER="${KIOSK_USER:-kiosk}"

log()  { printf '\e[1;34m==>\e[0m %s\n' "$*"; }
warn() { printf '\e[1;33m==> WARNING:\e[0m %s\n' "$*" >&2; }
die()  { printf '\e[1;31m==> ERROR:\e[0m %s\n' "$*" >&2; exit 1; }

require_root() {
    [[ $EUID -eq 0 ]] || die "run as root (sudo $0)"
}

# Print the packages in a list file, one per line ('#' comments and blank lines dropped).
read_package_list() {
    [[ -f $1 ]] || die "package list not found: $1"
    sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^[[:space:]]*$/d' "$1"
}

# Install every package listed in install/packages/<name>.txt.
pacman_install_list() {
    local list="$KIOSK_ROOT/install/packages/$1.txt" pkgs
    [[ -f $list ]] || die "package list not found: $list"
    mapfile -t pkgs < <(read_package_list "$list")
    pacman -S --needed --noconfirm "${pkgs[@]}"
}

# Copy one file from rootfs/ onto the target system, keeping its path.
install_rootfs_file() {
    local rel="$1" mode="${2:-0644}"
    install -Dm"$mode" "$KIOSK_ROOT/rootfs/$rel" "/$rel"
}
