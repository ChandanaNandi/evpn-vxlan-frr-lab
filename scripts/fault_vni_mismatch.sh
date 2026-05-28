#!/usr/bin/env bash
# Fault 2: change VNI on leaf2 to 99999. Control plane stays up but
# the data plane no longer matches between the leaves.
# Expected validator result: host_reachability fail.
set -e
docker exec leaf2 sh -c "
  set -e
  ip link del vni10010 2>/dev/null || true
  ip link add vni10010 type vxlan id 99999 dstport 4789 local 10.0.0.2 nolearning
  ip link set vni10010 master br10
  ip link set vni10010 up
"
echo "[fault] leaf2 VXLAN now using VNI 99999 instead of 10010"
