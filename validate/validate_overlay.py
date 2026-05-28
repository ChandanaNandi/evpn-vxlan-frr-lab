#!/usr/bin/env python3
"""End-to-end validator for the EVPN/VXLAN lab.

Prints a per-check pass/fail line and a final verdict. Exit code 0 only
when every check passes.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from checks import (  # noqa: E402
    bgp_underlay_established,
    bridge_fdb_has_her_to,
    container_running,
    evpn_imet_from_peer,
    host_can_ping,
    loopback_reachable,
    vxlan_iface_exists,
)


def _both(a, b):
    return a[0] and b[0], f"leaf1={a[1]} | leaf2={b[1]}"


def main():
    rows = []

    containers = ["spine1", "leaf1", "leaf2", "hostA", "hostB"]
    cstates = [(c, container_running(c)) for c in containers]
    all_up = all(s[0] for _, s in cstates)
    detail = ", ".join(f"{c}={s[1]}" for c, s in cstates)
    rows.append(("containers", all_up, detail))

    bgp_l1 = bgp_underlay_established("leaf1", "10.0.12.3")
    bgp_l2 = bgp_underlay_established("leaf2", "10.0.23.2")
    rows.append(("underlay_bgp", *_both(bgp_l1, bgp_l2)))

    lpb_l1 = loopback_reachable("leaf1", "10.0.0.2", source="10.0.0.1")
    lpb_l2 = loopback_reachable("leaf2", "10.0.0.1", source="10.0.0.2")
    rows.append(("leaf_loopbacks", *_both(lpb_l1, lpb_l2)))

    evpn_l1 = evpn_imet_from_peer("leaf1", "10.0.0.2")
    evpn_l2 = evpn_imet_from_peer("leaf2", "10.0.0.1")
    rows.append(("evpn_imet_routes", *_both(evpn_l1, evpn_l2)))

    vx_l1 = vxlan_iface_exists("leaf1")
    vx_l2 = vxlan_iface_exists("leaf2")
    rows.append(("vxlan_interfaces", *_both(vx_l1, vx_l2)))

    fdb_l1 = bridge_fdb_has_her_to("leaf1", "10.0.0.2")
    fdb_l2 = bridge_fdb_has_her_to("leaf2", "10.0.0.1")
    rows.append(("bridge_her_fdb", *_both(fdb_l1, fdb_l2)))

    ping = host_can_ping("hostA", "192.168.10.20")
    rows.append(("host_reachability", ping[0], ping[1]))

    name_w = max(len(r[0]) for r in rows)
    for name, ok, detail in rows:
        status = "pass" if ok else "fail"
        print(f"{name.ljust(name_w)}  {status}   {detail}")

    overall = all(ok for _, ok, _ in rows)
    print(f"verdict: {'overlay healthy' if overall else 'overlay unhealthy'}")
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
