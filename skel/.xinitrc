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
exec /usr/local/bin/kiosk-session
