#!/bin/sh
# No screen blanking, no DPMS: the shell decides when the screen sleeps.
xset s off -dpms
openbox &
exec /usr/local/bin/kiosk-session
