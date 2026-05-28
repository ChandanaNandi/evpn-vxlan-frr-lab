#!/usr/bin/env bash
# Undo any fault injection and rebuild the VXLAN data plane.
#
# We don't call setup_vxlan.sh from here because, post-setup, the host-facing
# leaf interfaces have been moved into br10 and have no IP -- setup_vxlan.sh's
# find_iface (which matches on docker-assigned IP prefix) would no longer
# locate them. Instead this script directly rebuilds vni10010 on each leaf
# without disturbing the bridge / host-port topology.
set -e
cd "$(dirname "$0")/.."

# Re-enable EVPN/BGP neighbors in case fault_evpn_neighbor_down.sh shut one
docker exec leaf1 vtysh -c "configure terminal" -c "router bgp 65001" \
  -c "no neighbor 10.0.12.3 shutdown" 2>/dev/null || true
docker exec leaf2 vtysh -c "configure terminal" -c "router bgp 65002" \
  -c "no neighbor 10.0.23.2 shutdown" 2>/dev/null || true

# Rebuild vni10010 on each leaf with the correct VNI and bridge attachment.
rebuild_vni() {
  local leaf=$1 vtep=$2
  docker exec "$leaf" sh -c "
    ip link show vni10010 >/dev/null 2>&1 && ip link del vni10010
    ip link add vni10010 type vxlan id 10010 dstport 4789 local $vtep nolearning
    ip link set vni10010 master br10
    ip link set vni10010 up
    bridge link set dev vni10010 neigh_suppress on learning off
  "
}
rebuild_vni leaf1 10.0.0.1
rebuild_vni leaf2 10.0.0.2

echo "[restore] back to baseline"
