#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

echo "[up] starting containers..."
docker compose up -d

echo "[up] waiting for FRR to settle..."
sleep 8

echo "[up] configuring VXLAN data plane..."
./scripts/setup_vxlan.sh

echo "[up] waiting for EVPN convergence..."
sleep 10

echo "[up] done. Run: ./validate/validate_overlay.py"
