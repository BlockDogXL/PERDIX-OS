#!/bin/sh
# No screen blanking, no DPMS: the shell decides when the screen sleeps.
xset s off -dpms
# PERDIX feather pointer (rootfs/usr/share/icons/PERDIX-Feather, made by tools/make-cursor-theme.py).
export XCURSOR_THEME=PERDIX-Feather XCURSOR_SIZE=32
# The user's own cursor choice, if any: ~/.config/perdix-os/cursor.conf (XCURSOR_THEME=, XCURSOR_SIZE=).
if [ -r "$HOME/.config/perdix-os/cursor.conf" ]; then
    . "$HOME/.config/perdix-os/cursor.conf"
    export XCURSOR_THEME XCURSOR_SIZE
fi
# Pass DISPLAY/XAUTHORITY to systemd --user and D-Bus (portals, PipeWire, Steam).
if [ -d /etc/X11/xinit/xinitrc.d ]; then
    for f in /etc/X11/xinit/xinitrc.d/?*.sh; do
        [ -x "$f" ] && . "$f"
    done
    unset f
fi
openbox &
# Re-apply the mouse acceleration curve (xinput resets at login) and start EasyEffects if sound
# effects are on, both chosen in PERDIX Control Center. In the background: EasyEffects can take a
# few seconds to start, and the desktop must not wait for it.
PYTHONPATH=/opt/kiosk-shell python -m kiosk_shell.perdix_hub --apply-saved >/dev/null 2>&1 &
exec /usr/local/bin/kiosk-session
