#!/usr/bin/env bash
# archiso profile for the PERDIX OS live ISO. Built by tools/build-iso.sh, which
# stages this directory together with rootfs/, skel/, shell/ and the package lists.
# shellcheck disable=SC2034

iso_name="perdix-os"
iso_label="PERDIX_$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y%m)"
iso_publisher="PERDIX OS"
iso_application="PERDIX OS gaming kiosk (live)"
iso_version="$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y.%m.%d)"
install_dir="arch"
bootmodes=('bios.syslinux'
           'uefi.systemd-boot')
pacman_conf="pacman.conf"
airootfs_image_type="squashfs"
airootfs_image_tool_options=('-comp' 'xz' '-Xbcj' 'x86' '-b' '1M' '-Xdict-size' '1M')
bootstrap_tarball_compression=('zstd' '-c' '-T0' '--long' '-19')
# airootfs is copied without modes or owners, so every executable and everything the
# kiosk user owns must be listed here. build-iso.sh fails if a file under
# /home/kiosk is missing from this list. UID/GID 1000 is fixed in etc/sysusers.d/kiosk.conf.
file_permissions=(
  ["/etc/shadow"]="0:0:400"
  ["/usr/local/bin/kiosk-session"]="0:0:755"
  ["/usr/local/bin/perdix-tune"]="0:0:755"
  ["/home/kiosk"]="1000:1000:750"
  ["/home/kiosk/.bash_profile"]="1000:1000:644"
  ["/home/kiosk/.xinitrc"]="1000:1000:755"
  ["/home/kiosk/.hushlogin"]="1000:1000:644"
)
