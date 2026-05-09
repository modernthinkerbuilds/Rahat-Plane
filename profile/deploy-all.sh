#!/usr/bin/env bash
# deploy-all.sh
#
# Runs both push scripts in sequence:
#   1. Profile README → modernthinkerbuilds/modernthinkerbuilds
#   2. Rahat-Plane README → modernthinkerbuilds/Rahat-Plane
#
# Each step is idempotent — safe to re-run.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

printf "\n========================================\n"
printf "  STEP 1 / 2 — Profile README\n"
printf "========================================\n\n"
bash "$DIR/push-profile-readme.sh"

printf "\n========================================\n"
printf "  STEP 2 / 2 — Rahat-Plane README\n"
printf "========================================\n\n"
bash "$DIR/push-rahat-readme.sh"

printf "\n\033[1;32m✓ Both deploys complete.\033[0m\n"
printf "  Profile: https://github.com/modernthinkerbuilds\n"
printf "  Rahat:   https://github.com/modernthinkerbuilds/Rahat-Plane\n\n"
