# Start the kiosk session on tty1 only, unless booted into maintenance mode.
if [[ -z "${DISPLAY:-}" && "$(tty)" == /dev/tty1 ]] && ! grep -qw kiosk.maintenance=1 /proc/cmdline; then
    exec startx -- -keeptty >/dev/null 2>&1
fi
