#!/usr/bin/env bash
# Fault 1: tear down the EVPN/BGP session from leaf1 -> spine1.
# Expected validator result: underlay_bgp fail, evpn_imet_routes fail,
# host_reachability fail.
set -e
docker exec leaf1 vtysh -c "configure terminal" \
  -c "router bgp 65001" -c "neighbor 10.0.12.3 shutdown"
echo "[fault] leaf1 BGP/EVPN neighbor 10.0.12.3 shut down"
