#!/usr/bin/env bash
# Run the shell in a normal window on a development machine.
set -euo pipefail
cd "$(dirname "$0")/../shell"
KIOSK_DEV=1 exec python -m kiosk_shell "$@"
