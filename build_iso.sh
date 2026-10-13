#!/usr/bin/env bash
# PERDIX OS - standalone archiso build script.
#
# Builds a live ISO from Arch's "releng" profile that boots silently and logs the
# unprivileged "kiosk" user straight into the PyQt5 desktop
# (shell/kiosk_shell/main_desktop.py), with Steam and Mesa/Vulkan preinstalled.
# From the repository it copies the desktop and Control Center (shell/kiosk_shell), the
# boot splash theme, and the Control Center's system files (the perdix-tune root helper,
# its polkit action, the uinput udev rule); everything else is inline.
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
python-evdev
python-matplotlib
xorg-xinput
xorg-server
xorg-xinit
xorg-xset
xorg-xrandr
openbox
noto-fonts
ttf-liberation
adwaita-cursors
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
# --- Office and drawing (desktop tiles, My Files): LibreOffice, Calibri/Cambria-compatible
#     fonts, Krita with the MyPaint brush engine; wmctrl brings their windows to the front ---
libreoffice-fresh
libreoffice-fresh-tr
hunspell-en_us
ttf-carlito
ttf-caladea
krita
libmypaint
mypaint-brushes
wmctrl
# --- System: network (NetworkManager + nmcli, Wi-Fi via wpa_supplicant), audio, power
#     permissions, antivirus, boot splash ---
networkmanager
wpa_supplicant
polkit
pipewire
pipewire-pulse
pipewire-alsa
wireplumber
lib32-pipewire
# Sound effects for the Control Center's Audio module (EasyEffects with LSP and Calf plugins);
# rtkit gives PipeWire real-time priority
easyeffects
calf
lsp-plugins-lv2
rtkit
# PERDIX Vision: games in gamescope (colour LUT, sharpening), python-xlib, numpy
gamescope
python-xlib
python-numpy
# Task Manager (CPU, memory, processes)
python-psutil
# Devices tab: lspci (driver in use) and lsusb
pciutils
usbutils
# System Diagnostics: vulkaninfo, glxinfo
vulkan-tools
mesa-utils
# Bluetooth: bluetoothd, bluetoothctl, Blueman; pipewire-audio has the Bluetooth audio plugin
bluez
bluez-utils
blueman
pipewire-audio
# Sharing: desktop portal for Flatpak apps and file dialogs
xdg-desktop-portal
xdg-desktop-portal-gtk
# Camera & Mic: v4l2-ctl; ffmpeg (with ffplay) for the camera preview and the Video Cutter
v4l-utils
ffmpeg
# Game Recording: OBS Studio (obs-websocket built in) in the background
obs-studio
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
# Bluetooth starts when an adapter shows up (what `systemctl enable bluetooth` does).
mkdir -p "$AIROOTFS/etc/systemd/system/bluetooth.target.wants"
ln -sf /usr/lib/systemd/system/bluetooth.service \
    "$AIROOTFS/etc/systemd/system/bluetooth.target.wants/bluetooth.service"
ln -sf /usr/lib/systemd/system/bluetooth.service "$AIROOTFS/etc/systemd/system/dbus-org.bluez.service"

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
m $KIOSK_USER perdix
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

# PERDIX feather pointer (rootfs/usr/share/icons/PERDIX-Feather, made by tools/make-cursor-theme.py).
export XCURSOR_THEME=PERDIX-Feather XCURSOR_SIZE=32
# The user's own cursor choice, if any: ~/.config/perdix-os/cursor.conf (XCURSOR_THEME=, XCURSOR_SIZE=).
if [ -r "$HOME/.config/perdix-os/cursor.conf" ]; then
    . "$HOME/.config/perdix-os/cursor.conf"
    export XCURSOR_THEME XCURSOR_SIZE
fi

# Pass DISPLAY to systemd --user and D-Bus (PipeWire, portals, Steam).
for f in /etc/X11/xinit/xinitrc.d/?*.sh; do
    [ -x "$f" ] && . "$f"
done
unset f

# Minimal window manager: lets Steam and games take focus and go full screen.
openbox &

# Re-apply the mouse acceleration curve (xinput resets at login) and start EasyEffects if sound
# effects are on, both chosen in PERDIX Control Center. In the background: EasyEffects can take a
# few seconds to start, and the desktop must not wait for it.
PYTHONPATH=/opt/kiosk-shell python -m kiosk_shell.perdix_hub --apply-saved >/dev/null 2>&1 &

# Session helpers from PERDIX Control Center: the Stream Mix follower (Sharing) and the recording
# hotkey (F9, OBS). Both stop by themselves when this session ends.
mkdir -p "$HOME/.local/state/perdix"
PYTHONPATH=/opt/kiosk-shell python -m kiosk_shell.services.sharing --follow >>"$HOME/.local/state/perdix/sharing.log" 2>&1 &
PYTHONPATH=/opt/kiosk-shell python -m kiosk_shell.services.recorder --hotkey >>"$HOME/.local/state/perdix/recording.log" 2>&1 &

# Launch the PERDIX desktop, and restart it if it ever exits or crashes.
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

# PERDIX Control Center system files: perdix-tune (root helper for the CPU governor, BBR and
# QoS, run through pkexec), its polkit action, and /dev/uinput access for Macro Studio.
# The PERDIX feather cursor theme (symlinks included).
mkdir -p "$AIROOTFS/usr/share/icons"
cp -a -- "$REPO_DIR/rootfs/usr/share/icons/PERDIX-Feather" "$AIROOTFS/usr/share/icons/"
# Default desktop wallpapers (grey partridges, Perdix perdix), shown in turn by the desktop.
mkdir -p "$AIROOTFS/usr/share/backgrounds"
cp -a -- "$REPO_DIR/rootfs/usr/share/backgrounds/perdix" "$AIROOTFS/usr/share/backgrounds/"
# The PERDIX file layout: /etc/perdix and /var/log/perdix (tmpfiles), the 'perdix' log-writer
# group (sysusers), and perdix-restore.service, which re-applies saved tuning at boot.
for file in usr/local/bin/perdix-tune \
            usr/share/polkit-1/actions/org.perdix.tune.policy \
            etc/udev/rules.d/60-perdix-uinput.rules \
            etc/modules-load.d/perdix-uinput.conf \
            etc/tmpfiles.d/perdix.conf \
            etc/sysusers.d/perdix.conf \
            etc/systemd/system/perdix-restore.service \
            usr/lib/libreoffice/share/registry/perdix.xcd \
            etc/xdg/kritarc \
            etc/locale.gen; do
    install -D -m 644 -- "$REPO_DIR/rootfs/$file" "$AIROOTFS/$file"
done
ln -sf /etc/systemd/system/perdix-restore.service \
    "$AIROOTFS/etc/systemd/system/multi-user.target.wants/perdix-restore.service"
# PERDIX Kılavuz (security audit): read by the Control Center's Kılavuz page and by
# `perdix-tune harden`, so it lives in a root-owned place.
install -D -m 644 -- "$REPO_DIR/apps/perdix-kilavuz/perdix_kilavuz.py" \
    "$AIROOTFS/usr/local/lib/perdix/perdix_kilavuz.py"

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
// The kiosk user may, without a password: power off, reboot, run perdix-tune (CPU
// governor, TCP congestion control, QoS, hardening) through pkexec, and manage Wi-Fi
// through NetworkManager. Nothing else.
polkit.addRule(function (action, subject) {
    var allowed = [
        "org.freedesktop.login1.power-off",
        "org.freedesktop.login1.power-off-multiple-sessions",
        "org.freedesktop.login1.reboot",
        "org.freedesktop.login1.reboot-multiple-sessions",
        "org.perdix.tune",
        // Network Settings in the Control Center (nmcli): join, scan, disconnect and forget
        // Wi-Fi networks and turn the radio on or off. Saving a Wi-Fi password writes a
        // system profile, which NetworkManager otherwise reserves for administrators.
        "org.freedesktop.NetworkManager.network-control",
        "org.freedesktop.NetworkManager.settings.modify.system",
        "org.freedesktop.NetworkManager.settings.modify.own",
        "org.freedesktop.NetworkManager.enable-disable-wifi",
        "org.freedesktop.NetworkManager.wifi.scan",
        // Region & Language in the Control Center: time zone, internet time, keyboard, language.
        "org.freedesktop.timedate1.set-timezone",
        "org.freedesktop.timedate1.set-ntp",
        "org.freedesktop.locale1.set-locale",
        "org.freedesktop.locale1.set-keyboard",
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
  ["/usr/local/bin/perdix-tune"]="0:0:755"
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
