#!/usr/bin/env bash
# CI gate 1: release-gate ids must be the GATE-xx set declared in platform_manifest.yaml.
# Fails on any legacy RG-xx id and on any GATE-xx id the manifest does not declare.
set -euo pipefail

cd "$(dirname "$0")/.."

manifest="docs/architecture/platform_manifest.yaml"
# Everything that may name a gate: docs, code, tests, CI config. This script names the
# legacy prefix on purpose, so it is left out of the scan.
scan=(docs app tests ci .github CLAUDE.md)
exclude=(--exclude=check_gate_ids.sh --exclude-dir=__pycache__)

[ -f "$manifest" ] || { echo "ERROR: $manifest not found"; exit 1; }

if grep -rnE "${exclude[@]}" '\bRG-[0-9]{2}\b' "${scan[@]}"; then
  echo "ERROR: legacy RG-xx gate ids found; use the GATE-xx ids from $manifest"
  exit 1
fi

declared=$(grep -oE 'GATE-[0-9]{2}[ab]?' "$manifest" | sort -u)
used=$(grep -rhoE "${exclude[@]}" 'GATE-[0-9]{2}[ab]?' "${scan[@]}" | sort -u)

[ -n "$declared" ] || { echo "ERROR: no GATE-xx ids declared in $manifest"; exit 1; }

unknown=$(comm -13 <(echo "$declared") <(echo "$used"))
if [ -n "$unknown" ]; then
  echo "ERROR: gate ids not declared in $manifest:"
  echo "$unknown"
  exit 1
fi

echo "Gate ids OK: $(echo "$declared" | wc -l | tr -d ' ') declared, none unknown, no RG-xx."
