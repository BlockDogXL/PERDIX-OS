#!/bin/sh
# No screen blanking, no DPMS: the shell decides when the screen sleeps.
xset s off -dpms
# Pass DISPLAY/XAUTHORITY to systemd --user and D-Bus (portals, PipeWire, Steam).
if [ -d /etc/X11/xinit/xinitrc.d ]; then
    for f in /etc/X11/xinit/xinitrc.d/?*.sh; do
        [ -x "$f" ] && . "$f"
    done
    unset f
fi
openbox &
# Re-apply the mouse acceleration curve chosen in PERDIX Control Center (xinput resets at login).
PYTHONPATH=/opt/kiosk-shell python -m kiosk_shell.perdix_hub --apply-saved >/dev/null 2>&1
exec /usr/local/bin/kiosk-session
