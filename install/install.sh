#!/usr/bin/env bash
# PERDIX OS installer: run on a fresh minimal Arch install, as root.
#   ./install.sh               run every step
#   ./install.sh --only 20     run only steps/20-*.sh
#   ./install.sh --skip 20     run every step except steps/20-*.sh
set -euo pipefail

source "$(dirname "$0")/lib/common.sh"
require_root

only="" skip=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --only) only="$2"; shift 2 ;;
        --skip) skip="$2"; shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done

exec > >(tee -a /var/log/kiosk-install.log) 2>&1

for step in "$KIOSK_ROOT"/install/steps/[0-9][0-9]-*.sh; do
    id="$(basename "$step" | cut -c1-2)"
    [[ -n $only && $id != "$only" ]] && continue
    [[ -n $skip && $id == "$skip" ]] && continue
    log "Running $(basename "$step")"
    bash "$step"
done

log "Done. Reboot to start the kiosk."
