#!/usr/bin/env bash
# Build the PERDIX OS live ISO with archiso (PLAN.md Phase 7).
#
# Run on an Arch Linux host with the archiso package installed:
#   sudo ./tools/build-iso.sh [options]
#
# Options:
#   --nvidia          add the proprietary NVIDIA driver (Turing / GTX 16xx and newer)
#   --root-password   prompt for a root password (default: root is locked)
#   --work DIR        build directory (default: ./work, needs ~10 GB free)
#   --out DIR         where the ISO is written (default: ./out)
#   --keep-work       keep the build directory afterwards
#
# The profile is assembled in DIR/profile from:
#   archiso/                 profiledef, pacman.conf, boot loader configs, ISO-only airootfs files
#   archiso/packages.*.txt   ISO-only packages (kernel, firmware, live-boot hooks)
#   install/packages/*.txt   the same package lists the installer uses
#   rootfs/                  system config shared with the installer (autologin, Xorg, polkit,
#                            Plymouth theme, ...)
#   skel/                    dotfiles for /home/kiosk
#   shell/kiosk_shell        the PyQt5 shell, installed to /opt/kiosk-shell
set -euo pipefail

source "$(dirname "$0")/../install/lib/common.sh"

MIN_ARCHISO_VERSION=86   # first release with the 'uefi.systemd-boot' boot mode
WORK_MARKER=.perdix-iso-work

# Package lists from install/packages/ that go onto the ISO. gpu-nvidia.txt is left
# out: it uses DKMS, which the live ISO replaces with archiso/packages.nvidia.txt.
ISO_PACKAGE_LISTS=(base boot gaming gpu-amd gpu-intel shell)

usage() { sed -n '2,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p}' "$0"; }

with_nvidia=0 ask_root_password=0 keep_work=0
work_dir="$KIOSK_ROOT/work"
out_dir="$KIOSK_ROOT/out"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --nvidia)        with_nvidia=1; shift ;;
        --root-password) ask_root_password=1; shift ;;
        --keep-work)     keep_work=1; shift ;;
        --work)          [[ $# -ge 2 ]] || die "--work needs a directory"; work_dir="$2"; shift 2 ;;
        --out)           [[ $# -ge 2 ]] || die "--out needs a directory"; out_dir="$2"; shift 2 ;;
        -h|--help)       usage; exit 0 ;;
        *)               die "unknown argument: $1 (see --help)" ;;
    esac
done

check_host() {
    require_root
    command -v pacman >/dev/null || die "this must run on an Arch Linux host"
    command -v mkarchiso >/dev/null || die "mkarchiso not found: pacman -S archiso"
    local version
    version="$(pacman -Q archiso | awk '{print $2}')"
    (( $(vercmp "$version" "$MIN_ARCHISO_VERSION") >= 0 )) \
        || die "archiso $version is too old, need $MIN_ARCHISO_VERSION or newer: pacman -Syu archiso"
    if (( ask_root_password )); then
        command -v openssl >/dev/null || die "--root-password needs openssl"
    fi
}

# Wipe the work directory, but only if it is empty or one we created: mkarchiso
# resumes from stale state, and --work could point anywhere.
prepare_work_dir() {
    if [[ -e $work_dir ]]; then
        if [[ -e $work_dir/$WORK_MARKER ]]; then
            rm -rf -- "$work_dir"
        elif [[ -n $(ls -A -- "$work_dir") ]]; then
            die "$work_dir is not empty and was not created by this script; pick another --work"
        fi
    fi
    mkdir -p -- "$work_dir"
    touch -- "$work_dir/$WORK_MARKER"
}

write_package_list() {
    local lists=("$KIOSK_ROOT/archiso/packages.live.txt") name
    for name in "${ISO_PACKAGE_LISTS[@]}"; do
        lists+=("$KIOSK_ROOT/install/packages/$name.txt")
    done
    (( with_nvidia )) && lists+=("$KIOSK_ROOT/archiso/packages.nvidia.txt")

    local list
    for list in "${lists[@]}"; do
        read_package_list "$list"
    done | sort -u >"$profile/packages.x86_64"
    log "$(wc -l <"$profile/packages.x86_64") packages from: ${lists[*]##*/}"
}

assemble_profile() {
    profile="$work_dir/profile"
    log "Assembling archiso profile in $profile"
    mkdir -p -- "$profile"
    cp -a -- "$KIOSK_ROOT/archiso/." "$profile/"
    # Shared system config. --update=none: ISO-specific files in archiso/airootfs take precedence.
    cp -a --update=none -- "$KIOSK_ROOT/rootfs/." "$profile/airootfs/"
    find "$profile/airootfs" -name __pycache__ -prune -exec rm -rf -- {} +

    local home="$profile/airootfs/home/kiosk"
    mkdir -p -- "$home"
    cp -a -- "$KIOSK_ROOT/skel/." "$home/"
    touch -- "$home/.hushlogin"

    mkdir -p -- "$profile/airootfs/opt/kiosk-shell"
    cp -a -- "$KIOSK_ROOT/shell/kiosk_shell" "$profile/airootfs/opt/kiosk-shell/"
    find "$profile/airootfs/opt/kiosk-shell" -name __pycache__ -prune -exec rm -rf -- {} +
    # PERDIX Kılavuz (security audit), used by the Control Center and `perdix-tune harden`.
    install -D -m 644 -- "$KIOSK_ROOT/apps/perdix-kilavuz/perdix_kilavuz.py" \
        "$profile/airootfs/usr/local/lib/perdix/perdix_kilavuz.py"
    # Shown on the desktop's About System page.
    printf 'VERSION="%s"\nEDITION="Live ISO"\n' "$(date +%Y.%m.%d)" >"$profile/airootfs/etc/perdix-release"

    write_package_list

    if (( ask_root_password )); then
        set_root_password
    fi
}

# Every file under /home/kiosk must have an owner in profiledef.sh, or mkarchiso
# leaves it owned by root and the kiosk user can't write its own home.
check_home_permissions() {
    local missing
    missing="$(
        declare -A file_permissions=()
        source "$profile/profiledef.sh"
        cd "$profile/airootfs"
        find home/kiosk | while read -r path; do
            [[ -n ${file_permissions["/$path"]+set} ]] || echo "  /$path"
        done
    )"
    [[ -z $missing ]] || die "add these to file_permissions in archiso/profiledef.sh:"$'\n'"$missing"
}

set_root_password() {
    local pw pw2
    read -rsp "Root password for the ISO: " pw; echo
    read -rsp "Repeat: " pw2; echo
    [[ -n $pw && $pw == "$pw2" ]] || die "passwords are empty or do not match"
    printf 'root:%s:14871::::::\n' "$(openssl passwd -6 -stdin <<<"$pw")" >"$profile/airootfs/etc/shadow"
}

main() {
    check_host
    prepare_work_dir
    assemble_profile
    check_home_permissions

    mkdir -p -- "$out_dir"
    log "Running mkarchiso (this downloads several GB on the first run)"
    mkarchiso -v -w "$work_dir/mkarchiso" -o "$out_dir" "$profile"

    # Hand the ISO back to the user who ran sudo.
    if [[ -n ${SUDO_UID:-} ]]; then
        chown "$SUDO_UID:$SUDO_GID" -- "$out_dir" "$out_dir"/*.iso
    fi
    (( keep_work )) || rm -rf -- "$work_dir"

    log "Done:"
    ls -lh -- "$out_dir"/*.iso
}

main
