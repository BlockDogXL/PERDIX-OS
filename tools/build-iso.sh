#!/usr/bin/env bash
# Build the PERDIX OS ISO from archiso/ (PLAN.md Phase 7). Requires the archiso package.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "TODO: archiso profile not created yet (PLAN.md Phase 7)" >&2
exit 1
# sudo mkarchiso -v -w /tmp/kiosk-iso-work -o out/ archiso/
