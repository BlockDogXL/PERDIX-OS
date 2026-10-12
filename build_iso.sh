#!/usr/bin/env bash
# PERDIX OS - standalone archiso build script.
#
# Builds a live ISO from Arch's "releng" profile that boots silently and logs the
# unprivileged "kiosk" user straight into the PyQt5 desktop
# (shell/kiosk_shell/main_desktop.py), with Steam and Mesa/Vulkan preinstalled.
# From the repository it copies only the desktop (shell/kiosk_shell) and the boot
# splash theme (rootfs/usr/share/plymouth/themes/perdix); everything else is inline.
#
# Usage:   sudo ./build_iso.sh [--nvidia]
#          --nvidia  add NVIDIA's proprietary driver (GTX 16xx / RTX 20xx and newer)
# Needs:   an Arch Linux host with archiso >= 86 and ~10 GB free in BUILD_DIR.
# Output:  out/perdix-os-<date>-x86_64.iso
#
# Live-ISO limits: everything written at runtime (Steam client, games) lives in a
# 4 GB RAM overlay and is gone after a reboot. Proton is not a pacman package;
# Steam downloads it the first time a Windows game is started.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${BUILD_DIR:-$REPO_DIR/build}"   # not /tmp: it is often RAM-backed and too small
OUT_DIR="${OUT_DIR:-$REPO_DIR/out}"
RELENG="${RELENG:-/usr/share/archiso/configs/releng}"
PROFILE="$BUILD_DIR/profile"
AIROOTFS="$PROFILE/airootfs"
KIOSK_USER=kiosk
KIOSK_UID=1000
BOOT_PARAMS="cow_spacesize=4G quiet splash loglevel=3 rd.udev.log_level=3 systemd.show_status=auto vt.global_cursor_default=0"

msg() { echo "[+] $*"; }
die() { echo "[!] $*" >&2; exit 1; }

with_nvidia=0
case "${1:-}" in
    --nvidia) with_nvidia=1 ;;
    "") ;;
    *) die "usage: sudo $0 [--nvidia]" ;;
esac

# --- Checks -----------------------------------------------------------------------------------
[[ $EUID -eq 0 ]] || die "run as root: sudo $0"
command -v mkarchiso >/dev/null || die "archiso is not installed: pacman -S archiso"
archiso_version="$(pacman -Q archiso | cut -d' ' -f2)"
(( $(vercmp "$archiso_version" 86) >= 0 )) || die "archiso $archiso_version is too old, need 86+: pacman -Syu archiso"
[[ -f $REPO_DIR/shell/kiosk_shell/main_desktop.py ]] || die "shell/kiosk_shell/main_desktop.py not found next to this script"
PLYMOUTH_THEME="$REPO_DIR/rootfs/usr/share/plymouth/themes/perdix"
[[ -f $PLYMOUTH_THEME/perdix.script ]] || die "Plymouth theme not found in $PLYMOUTH_THEME"

msg "Starting PERDIX OS ISO build..."

# Only delete a build directory this script created (BUILD_DIR can come from the environment).
if [[ -e $BUILD_DIR ]]; then
    [[ -e $BUILD_DIR/.perdix-build ]] || die "$BUILD_DIR exists and was not created by this script"
    rm -rf -- "$BUILD_DIR"
fi
mkdir -p -- "$BUILD_DIR" "$OUT_DIR"   # OUT_DIR is kept, so earlier ISOs survive
touch -- "$BUILD_DIR/.perdix-build"
cp -r -- "$RELENG" "$PROFILE"

# --- Profile identity -------------------------------------------------------------------------
sed -i -e 's/^iso_name=.*/iso_name="perdix-os"/' \
       -e 's/^iso_label="ARCH_/iso_label="PERDIX_/' \
       -e 's/^iso_publisher=.*/iso_publisher="PERDIX OS"/' \
       -e 's/^iso_application=.*/iso_application="PERDIX OS live"/' \
       "$PROFILE/profiledef.sh"

# --- [multilib]: Steam and the 32-bit drivers live there ---------------------------------------
sed -i '/^#\[multilib\]$/,/^#Include/ s/^#//' "$PROFILE/pacman.conf"
grep -q '^\[multilib\]$' "$PROFILE/pacman.conf" || die "could not enable [multilib] in pacman.conf"

# --- Packages ---------------------------------------------------------------------------------
msg "Adding packages..."
{
    cat "$PROFILE/packages.x86_64"
    cat <<'EOF'
# --- PERDIX OS: desktop ---
python
python-pyqt5
python-matplotlib
xorg-server
xorg-xinit
xorg-xset
xorg-xrandr
openbox
noto-fonts
ttf-liberation
# --- Graphics: Mesa + Vulkan, 64- and 32-bit (AMD, Intel; NVK for NVIDIA) ---
mesa
lib32-mesa
vulkan-radeon
lib32-vulkan-radeon
vulkan-intel
lib32-vulkan-intel
vulkan-nouveau
vulkan-icd-loader
lib32-vulkan-icd-loader
# --- Gaming ---
steam
gamemode
lib32-gamemode
mangohud
lib32-mangohud
cpupower
# --- System: network, audio, power permissions, antivirus, boot splash ---
networkmanager
polkit
pipewire
pipewire-pulse
pipewire-alsa
wireplumber
lib32-pipewire
clamav
plymouth
EOF
    if (( with_nvidia )); then
        # nvidia-open is prebuilt for the "linux" kernel the ISO boots.
        printf '%s\n' nvidia-open nvidia-utils lib32-nvidia-utils
    fi
} | sed -e 's/#.*//' -e '/^[[:space:]]*$/d' | sort -u >"$BUILD_DIR/packages"
mv -- "$BUILD_DIR/packages" "$PROFILE/packages.x86_64"

# --- Remove releng's live-installer services ---------------------------------------------------
# sshd (root has an empty password in releng), iwd and systemd-networkd (NetworkManager
# replaces them), and the speech-synthesis helpers.
rm -f -- \
    "$AIROOTFS/etc/systemd/system/multi-user.target.wants/sshd.service" \
    "$AIROOTFS/etc/systemd/system/multi-user.target.wants/iwd.service" \
    "$AIROOTFS/etc/systemd/system/multi-user.target.wants/systemd-networkd.service" \
    "$AIROOTFS/etc/systemd/system/sockets.target.wants/systemd-networkd.socket" \
    "$AIROOTFS/etc/systemd/system/network-online.target.wants/systemd-networkd-wait-online.service" \
    "$AIROOTFS/etc/systemd/system/multi-user.target.wants/livecd-talk.service" \
    "$AIROOTFS/etc/systemd/system/sound.target.wants/livecd-alsa-unmuter.service"
ln -sf /usr/lib/systemd/system/NetworkManager.service \
    "$AIROOTFS/etc/systemd/system/multi-user.target.wants/NetworkManager.service"
# Keeps ClamAV's virus definitions up to date for the "Antivirus Scan" button.
ln -sf /usr/lib/systemd/system/clamav-freshclam.service \
    "$AIROOTFS/etc/systemd/system/multi-user.target.wants/clamav-freshclam.service"

# Lock root: nobody gets a root login on the kiosk.
printf 'root:*:14871::::::\n' >"$AIROOTFS/etc/shadow"

# --- Kiosk user and autologin -----------------------------------------------------------------
# Steam refuses to run as root, so the desktop runs as an unprivileged user.
msg "Creating the $KIOSK_USER user and autologin..."
mkdir -p "$AIROOTFS/etc/sysusers.d"
cat >"$AIROOTFS/etc/sysusers.d/$KIOSK_USER.conf" <<EOF
u $KIOSK_USER $KIOSK_UID "PERDIX kiosk" /home/$KIOSK_USER /bin/bash
m $KIOSK_USER video
m $KIOSK_USER audio
m $KIOSK_USER input
EOF

# Replaces releng's root autologin (same file name).
mkdir -p "$AIROOTFS/etc/systemd/system/getty@tty1.service.d"
cat >"$AIROOTFS/etc/systemd/system/getty@tty1.service.d/autologin.conf" <<EOF
[Service]
ExecStart=
ExecStart=-/usr/bin/agetty -o '-p -f -- \\\\u' --noclear --noissue --nohints --autologin $KIOSK_USER %I \$TERM
EOF

home="$AIROOTFS/home/$KIOSK_USER"
mkdir -p "$home"
touch "$home/.hushlogin"   # no motd / last-login text

cat >"$home/.bash_profile" <<'EOF'
# Start X on tty1 only. The maintenance boot entry (kiosk.maintenance=1) stays on the console.
if [[ -z ${DISPLAY:-} && $(tty) == /dev/tty1 ]] && ! grep -qw kiosk.maintenance=1 /proc/cmdline; then
    exec startx -- -keeptty >/dev/null 2>&1
fi
EOF

cat >"$home/.xinitrc" <<'EOF'
#!/bin/sh
# No screensaver, no blanking, no DPMS.
xset s off
xset -dpms
xset s noblank

# Pass DISPLAY to systemd --user and D-Bus (PipeWire, portals, Steam).
for f in /etc/X11/xinit/xinitrc.d/?*.sh; do
    [ -x "$f" ] && . "$f"
done
unset f

# Minimal window manager: lets Steam and games take focus and go full screen.
openbox &

# Launch the PERDIX desktop, and restart it if it ever exits or crashes.
mkdir -p "$HOME/.local/state/perdix"
while true; do
    PYTHONPATH=/opt/kiosk-shell python -m kiosk_shell >>"$HOME/.local/state/perdix/desktop.log" 2>&1
    echo "$(date -Is) desktop exited, restarting" >>"$HOME/.local/state/perdix/desktop.log"
    sleep 2
done
EOF

# --- The desktop itself -----------------------------------------------------------------------
msg "Copying the PERDIX desktop..."
mkdir -p "$AIROOTFS/opt/kiosk-shell"
cp -r -- "$REPO_DIR/shell/kiosk_shell" "$AIROOTFS/opt/kiosk-shell/"
find "$AIROOTFS/opt/kiosk-shell" -name __pycache__ -prune -exec rm -rf -- {} +

# Shown on the desktop's About System page.
printf 'VERSION="%s"\nEDITION="Live ISO"\n' "$(date +%Y.%m.%d)" >"$AIROOTFS/etc/perdix-release"

# --- Lockdown ---------------------------------------------------------------------------------
mkdir -p "$AIROOTFS/etc/X11/xorg.conf.d" "$AIROOTFS/etc/polkit-1/rules.d" "$AIROOTFS/etc/sysctl.d"
cat >"$AIROOTFS/etc/X11/xorg.conf.d/10-kiosk.conf" <<'EOF'
# No Ctrl+Alt+F1..F12 console switching, no Ctrl+Alt+Backspace.
Section "ServerFlags"
    Option "DontVTSwitch" "true"
    Option "DontZap"      "true"
EndSection
EOF

cat >"$AIROOTFS/etc/polkit-1/rules.d/50-kiosk-power.rules" <<EOF
// The kiosk user may power off and reboot without a password, nothing else.
polkit.addRule(function (action, subject) {
    var allowed = [
        "org.freedesktop.login1.power-off",
        "org.freedesktop.login1.power-off-multiple-sessions",
        "org.freedesktop.login1.reboot",
        "org.freedesktop.login1.reboot-multiple-sessions",
    ];
    if (subject.user === "$KIOSK_USER" && allowed.indexOf(action.id) >= 0) {
        return polkit.Result.YES;
    }
});
EOF

cat >"$AIROOTFS/etc/sysctl.d/80-kiosk.conf" <<'EOF'
vm.max_map_count = 2147483642
kernel.printk = 3 3 3 3
kernel.sysrq = 0
EOF

# airootfs is copied without owners or modes, so set them here.
cat >>"$PROFILE/profiledef.sh" <<EOF
file_permissions+=(
  ["/home/$KIOSK_USER"]="$KIOSK_UID:$KIOSK_UID:750"
  ["/home/$KIOSK_USER/.bash_profile"]="$KIOSK_UID:$KIOSK_UID:644"
  ["/home/$KIOSK_USER/.xinitrc"]="$KIOSK_UID:$KIOSK_UID:755"
  ["/home/$KIOSK_USER/.hushlogin"]="$KIOSK_UID:$KIOSK_UID:644"
)
EOF

# --- Silent boot ------------------------------------------------------------------------------
msg "Configuring silent boot..."
# Plymouth splash with the Piri theme (red-and-white, then orange glow), in the initramfs.
mkdir -p "$AIROOTFS/usr/share/plymouth/themes" "$AIROOTFS/etc/plymouth"
cp -r -- "$PLYMOUTH_THEME" "$AIROOTFS/usr/share/plymouth/themes/"
cp -- "$REPO_DIR/rootfs/etc/plymouth/plymouthd.conf" "$AIROOTFS/etc/plymouth/plymouthd.conf"
sed -i '/^HOOKS=/ s/\bkms\b/kms plymouth/' "$AIROOTFS/etc/mkinitcpio.conf.d/archiso.conf"
grep -q '^HOOKS=.*plymouth' "$AIROOTFS/etc/mkinitcpio.conf.d/archiso.conf" || die "could not add the plymouth hook"
# Quiet kernel command line on every boot entry.
sed -i "/archisobasedir=/ s|\$| $BOOT_PARAMS|" \
    "$PROFILE"/efiboot/loader/entries/*.conf "$PROFILE"/syslinux/*.cfg "$PROFILE"/grub/*.cfg
# Hide the boot menus: hold Space (UEFI) or press a key (BIOS) during power-on to see them.
sed -i 's/^timeout .*/timeout 0/' "$PROFILE/efiboot/loader/loader.conf"
sed -i 's/^TIMEOUT .*/TIMEOUT 10\nMENU HIDDEN/' "$PROFILE/syslinux/archiso_sys.cfg"
sed -i 's/^timeout=.*/timeout=0/' "$PROFILE"/grub/*.cfg

# --- Build ------------------------------------------------------------------------------------
msg "Packages and configuration added. Running mkarchiso (several GB to download)..."
mkarchiso -v -w "$BUILD_DIR/work" -o "$OUT_DIR" "$PROFILE"

# Give the ISO back to the user who ran sudo.
if [[ -n ${SUDO_UID:-} ]]; then
    chown "$SUDO_UID:$SUDO_GID" -- "$OUT_DIR" "$OUT_DIR"/*.iso
fi
rm -rf -- "$BUILD_DIR"

msg "Done! The ISO is in $OUT_DIR:"
ls -lh -- "$OUT_DIR"/*.iso
