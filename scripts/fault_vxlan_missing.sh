#!/usr/bin/env bash
# Fault 3: delete the VXLAN interface on leaf2 entirely.
# Expected validator result: vxlan_interfaces fail, host_reachability fail.
set -e
docker exec leaf2 ip link del vni10010
echo "[fault] leaf2 vni10010 interface deleted"
