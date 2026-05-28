#!/usr/bin/env bash
# Configure the VXLAN data plane on both leaves and the L2 stretch
# at the host containers.
#
# On each leaf:
#   - create bridge br10
#   - create vni10010 (VXLAN id 10010) bound to the loopback VTEP IP
#   - put the host-facing docker interface into br10 (no L3 address)
#
# On each host:
#   - flush the docker-assigned IP on the leaf-facing iface
#   - assign 192.168.10.{10,20}/24 so hostA and hostB share an L2 subnet

set -e

# Find the interface inside a container whose first IPv4 address
# starts with the given prefix (e.g. "169.254.10.").
find_iface() {
  local cname=$1
  local prefix=$2
  docker exec "$cname" sh -c "ip -o -4 addr show | awk '{print \$2, \$4}'" \
    | awk -v p="$prefix" 'index($2, p) == 1 {print $1; exit}'
}

setup_leaf() {
  local leaf=$1
  local vtep=$2
  local host_prefix=$3

  local host_if
  host_if=$(find_iface "$leaf" "$host_prefix")
  if [ -z "$host_if" ]; then
    echo "[$leaf] could not find host-facing interface with prefix $host_prefix" >&2
    return 1
  fi
  echo "[$leaf] host-facing iface: $host_if, VTEP $vtep"

  # Bridge learning is forced OFF on both ports.
  # Without proper EVPN split-horizon (which on Linux requires a vlan-aware bridge
  # with vlan_tunnel + tunnel_info mapping that the LinuxKit kernel in Docker
  # Desktop doesn't deliver decap'd frames through correctly), enabling bridge
  # learning leads to a MAC-mobility ping-pong: leaf1 advertises hostA.MAC as
  # local, leaf2 sees the looped broadcast and also learns hostA.MAC locally,
  # they fight over the type-2 sequence number, and unicast is blackholed.
  # With learning off, FRR has no local MACs to advertise, so the ping-pong
  # cannot start; BUM (including unknown unicast) is flooded via the HER entry
  # FRR installs from type-3 IMET routes, which is enough for hostA<->hostB ARP
  # and eventually ICMP to flow. See README "Limitations" section.
  docker exec "$leaf" sh -c "
    set -e
    ip link show br10 >/dev/null 2>&1 || ip link add br10 type bridge
    ip link set br10 up

    ip link show vni10010 >/dev/null 2>&1 && ip link del vni10010
    ip link add vni10010 type vxlan id 10010 dstport 4789 local $vtep nolearning
    ip link set vni10010 master br10
    ip link set vni10010 up
    bridge link set dev vni10010 neigh_suppress on learning off

    ip addr flush dev $host_if || true
    ip link set $host_if master br10
    ip link set $host_if up
    bridge link set dev $host_if learning off

    # Drop anything the bridge learned before we set learning=off.
    for m in \$(bridge fdb show | awk '\$NF == \"master\" && \$(NF-1) == \"br10\" {print \$1}' | sort -u); do
      bridge fdb del \$m dev $host_if master 2>/dev/null || true
    done
  "
}

setup_host() {
  local host=$1
  local ip=$2
  local docker_prefix=$3

  local ifname
  ifname=$(find_iface "$host" "$docker_prefix")
  if [ -z "$ifname" ]; then
    echo "[$host] could not find interface with prefix $docker_prefix" >&2
    return 1
  fi
  echo "[$host] overlay iface: $ifname  -> $ip/24"

  docker exec "$host" sh -c "
    ip addr flush dev $ifname
    ip addr add $ip/24 dev $ifname
    ip link set $ifname up
  "
}

setup_leaf leaf1 10.0.0.1 169.254.10.
setup_leaf leaf2 10.0.0.2 169.254.20.

setup_host hostA 192.168.10.10 169.254.10.
setup_host hostB 192.168.10.20 169.254.20.

echo "[setup_vxlan] complete"
