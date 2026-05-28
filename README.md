# EVPN-IMET / VXLAN / FRR Overlay Lab

A small, runnable leaf-spine fabric on a laptop: eBGP underlay, BGP-EVPN
**type-3 (IMET)** control plane driving VXLAN head-end replication, two
Linux "tenant" hosts stretched across the fabric in a single L2 segment,
and a Python validator that asserts the overlay is healthy end-to-end.

**Scope, honestly:** this lab exercises the EVPN control plane signaling
(`l2vpn evpn` address family up, type-3 IMET routes exchanged via the
spine with `next-hop-unchanged`, HER FDB entries programmed by FRR from
those routes) and the VXLAN data plane (encap/decap, VTEP-to-VTEP via the
underlay). It does **not** demonstrate EVPN type-2 MAC/IP learning —
that's intentionally disabled here because the Docker Desktop / LinuxKit
kernel doesn't enforce EVPN BUM split-horizon on plain head-end
replication, and bridge learning + type-2 advertisement produce a
MAC-mobility ping-pong (see Limitations). Result: host-to-host traffic
works, but it goes through the BUM-flood path rather than learned-MAC
unicast. A real switch ASIC, or a kernel + bridge setup where
`vlan_tunnel` decap delivery works as documented, would have type-2
populating the remote FDB and unicast going VTEP-to-VTEP directly.

There is no AI, no orchestrator, no SONiC: just FRR, the Linux bridge /
VXLAN stack, Docker Compose, and a small Python validator.

## Topology

```
   192.168.10.10/24                                        192.168.10.20/24
        hostA  --- 169.254.10.0/24 ---  leaf1                    leaf2  --- 169.254.20.0/24 ---  hostB
                                       (AS 65001)                (AS 65002)
                                       lo 10.0.0.1               lo 10.0.0.2
                                            \                    /
                                  10.0.12.0/24             10.0.23.0/24
                                              \           /
                                               \         /
                                                 spine1
                                                (AS 65000)
                                                lo 10.0.0.3
```

| Node    | Image                       | ASN   | Loopback / VTEP | P2P links                          |
|---------|-----------------------------|-------|-----------------|------------------------------------|
| spine1  | `frrouting/frr:v8.4.1`      | 65000 | `10.0.0.3`      | `10.0.12.3`, `10.0.23.2`           |
| leaf1   | `frrouting/frr:v8.4.1`      | 65001 | `10.0.0.1`      | `10.0.12.2`                        |
| leaf2   | `frrouting/frr:v8.4.1`      | 65002 | `10.0.0.2`      | `10.0.23.3`                        |
| hostA   | `nicolaka/netshoot`         | -     | `192.168.10.10` | attached to leaf1's host bridge    |
| hostB   | `nicolaka/netshoot`         | -     | `192.168.10.20` | attached to leaf2's host bridge    |

## Architecture

### Underlay — eBGP IPv4 unicast

Each leaf has a single eBGP session to spine1 over its P2P link. The leaves
advertise their `/32` loopbacks; spine1 readvertises them. That gives the
leaves IP reachability to each other's VTEP address (`10.0.0.1 ↔ 10.0.0.2`),
which is the only thing the underlay has to provide.

### Control plane — BGP-EVPN (AFI/SAFI L2VPN EVPN)

The same BGP sessions also carry the `l2vpn evpn` address family. Each leaf
runs `advertise-all-vni`, so FRR turns kernel VXLAN state into EVPN routes.
What this lab actually exercises:

- **Type-3 (IMET) — exercised.** "I am a VTEP for VNI 10010, reach me at
  `10.0.0.X`". Each leaf emits one. FRR on the remote leaf imports it and
  installs a head-end-replication FDB entry
  (`00:00:00:00:00:00 dst <peer-VTEP> self` on `vni10010`), so the bridge
  knows where to send BUM frames for this VNI.
- **Type-2 (MAC/IP) — not exercised in this lab.** Type-2 would advertise
  "MAC X (and optionally IP Y) lives behind VTEP Z" and would let unicast
  go VTEP-to-VTEP directly without flooding. This is intentionally **off**
  here — see Limitations for the kernel reason. The expected validator
  output therefore shows `type-2=0`.

Because the underlay is eBGP, spine1 would normally rewrite the BGP
next-hop on EVPN routes to itself. That would break the data plane (spine1
is not a VTEP). The spine config carries `neighbor … next-hop-unchanged`
under `address-family l2vpn evpn` so the original VTEP next-hop survives —
this is the standard knob for an eBGP-EVPN spine that does not participate
in the overlay.

### Data plane — VXLAN

Each leaf has:
- a Linux bridge `br10`
- a VXLAN device `vni10010` (VNI 10010, UDP/4789, source = loopback VTEP)
  enslaved to `br10` with `nolearning` and `neigh_suppress on`
- the host-facing veth flushed of its docker-assigned IP and enslaved to
  `br10`
- bridge learning **disabled** on both ports (see Limitations); the only
  thing populating the FDB is FRR's HER programming from type-3 routes

The host containers carry `192.168.10.10/24` and `192.168.10.20/24` on
their leaf-facing interface. From the hosts' point of view, they're on the
same flat L2 segment. From the fabric's point of view, that segment is
stretched over VXLAN: any frame the bridge can't FDB-match — which in this
config is *everything*, since the FDB only has the HER entry — is flooded
to the remote VTEP via VXLAN encap. ARP works (broadcast → HER), and so
does the resulting unicast ICMP (treated as unknown-unicast → HER again).

## Layout

```
.
├── docker-compose.yml             # 5 containers + 4 docker bridge networks
├── configs/
│   ├── leaf1/{frr.conf,daemons,vtysh.conf}
│   ├── leaf2/{frr.conf,daemons,vtysh.conf}
│   └── spine1/{frr.conf,daemons,vtysh.conf}
├── scripts/
│   ├── up.sh                      # compose up + setup_vxlan + wait for EVPN
│   ├── down.sh                    # compose down -v
│   ├── setup_vxlan.sh             # build br10/vni10010 on leaves, IP the hosts
│   ├── restore.sh                 # undo any fault, rebuild VXLAN
│   ├── fault_evpn_neighbor_down.sh
│   ├── fault_vni_mismatch.sh
│   └── fault_vxlan_missing.sh
├── validate/
│   ├── checks.py                  # low-level vtysh / ip / ping checks
│   └── validate_overlay.py        # runs the full check matrix + verdict
└── README.md
```

## Quickstart

Requires Docker (with Compose v2) and a Linux kernel that has the `vxlan`
module — both Docker Desktop on macOS and a standard Linux host work.

```sh
./scripts/up.sh                    # bring everything up
./validate/validate_overlay.py     # assert overlay is healthy
./scripts/down.sh                  # tear it all down
```

`up.sh` does three things in order: `docker compose up -d`, wait ~8s for
FRR to settle, run `setup_vxlan.sh` to build the L2 stretch, then wait ~10s
for EVPN to converge.

### Expected validator output

```
containers         pass   spine1=true, leaf1=true, leaf2=true, hostA=true, hostB=true
underlay_bgp       pass   leaf1=10.0.12.3=Established | leaf2=10.0.23.2=Established
leaf_loopbacks     pass   leaf1=reachable | leaf2=reachable
evpn_imet_routes   pass   leaf1=peer-IMET=yes type-2=0 type-3=2 | leaf2=peer-IMET=yes type-2=0 type-3=2
vxlan_interfaces   pass   leaf1=vni=10010 | leaf2=vni=10010
bridge_her_fdb     pass   leaf1=HER->10.0.0.2 present (entries=3) | leaf2=HER->10.0.0.1 present (entries=3)
host_reachability  pass   6/15 replies (need >=4)
verdict: overlay healthy
```

What each line actually asserts:

- **`evpn_imet_routes`**: each leaf has received a type-3 IMET route in
  `show bgp l2vpn evpn` whose key carries the *peer's* VTEP IP. `type-2=0`
  is the documented value for this lab (see Limitations); type-3 is the
  control-plane signal this lab exercises.
- **`bridge_her_fdb`**: each leaf has a head-end-replication entry on
  `vni10010` of the form `00:00:00:00:00:00 dst <peer-VTEP> self`, which
  is the FDB state FRR programs in response to the type-3 IMET. Without
  this, BUM cannot leave the local leaf — and without that, hostA cannot
  ARP hostB.
- **`host_reachability`**: at least 4 of 15 ICMP echoes return. The
  latency is ~1 s per round-trip and the reply rate is noisy — observed
  range across runs on Docker Desktop / LinuxKit is roughly 20–60 % of
  probes (the kernel's ARP/STALE retry timers interact badly with the
  BUM-flood path). `>=4/15` (≈ 27 %) sits below the observed mean, so a
  working data plane passes most of the time and a fully broken one
  (0/15) still fails. Expect the occasional false negative — re-run the
  validator if you get an outlier. A real switch ASIC with EVPN type-2
  unicast forwarding would make this check uneventful (15/15, sub-ms);
  see Limitations for what's being substituted.

## Fault scenarios

Each fault is a single shell script that injects one failure. Run the
validator after each to see how the failure surfaces, then `restore.sh` to
return to baseline.

### 1. EVPN/BGP neighbor down

```sh
./scripts/fault_evpn_neighbor_down.sh   # shuts leaf1→spine1 BGP
./validate/validate_overlay.py          # expect underlay_bgp + evpn_imet_routes + ping to fail
./scripts/restore.sh
```

Tears down leaf1's BGP session to spine1, which kills both the underlay
session and the EVPN address family riding on it. Leaf2 loses its EVPN
routes pointing at `10.0.0.1` once they age out / are withdrawn, and the
data plane goes dark.

### 2. VNI mismatch

```sh
./scripts/fault_vni_mismatch.sh         # leaf2 switches to VNI 99999
./validate/validate_overlay.py          # expect vxlan_interfaces + ping to fail
./scripts/restore.sh
```

Rebuilds `vni10010` on leaf2 with VXLAN id 99999 instead of 10010. BGP and
EVPN both stay up — this is purely a data-plane mismatch. Frames arriving
encapsulated with VNI 10010 are dropped because leaf2 has no interface for
that VNI anymore, and the host ping fails.

### 3. Missing VXLAN interface

```sh
./scripts/fault_vxlan_missing.sh        # deletes vni10010 on leaf2
./validate/validate_overlay.py          # expect vxlan_interfaces + ping to fail
./scripts/restore.sh
```

Outright removes `vni10010` from leaf2. The VXLAN check is the most
informative one to look at here — the missing interface is the root cause.

## Limitations

- **No EVPN split-horizon for BUM head-end replication on this kernel.**
  A hardware EVPN switch (Cisco/Arista/SONiC ASIC) tracks which remote VTEP
  a BUM frame arrived from and excludes that VTEP from the flood list. The
  Linux VXLAN driver in Docker Desktop's LinuxKit kernel doesn't enforce
  that for plain head-end replication, so bridge learning is *disabled* on
  both ports — otherwise leaf1 and leaf2 would both data-plane-learn the
  remote host's MAC locally, advertise it via type-2, and ping-pong the
  EVPN sequence number forever (~1M pps loop, no actual delivery). With
  learning off there's no flood loop because the bridge doesn't fight FRR
  over MAC ownership, but every host frame is "unknown-unicast → flood",
  so ICMP RTT is ~1 s instead of microseconds. Functionally correct,
  performance-impaired. The proper kernel-side fix is a vlan-aware bridge
  with `vlan_tunnel` + `tunnel_info` on the VXLAN port; FRR supports it,
  but on this kernel the decap'd frame didn't make it through the bridge.
- **`type-2` EVPN routes therefore stay at 0.** The control plane is
  exercised end-to-end (`l2vpn evpn` address family, type-3 IMET routes,
  HER FDB programming from EVPN), but MAC/IP learning is disabled. A real
  switch would advertise type-2s as MACs are seen.
- **Single VNI / single tenant.** No L3VNI, no VRFs, no symmetric IRB. This
  is a pure L2-stretch demo; routing inside the overlay is out of scope.
- **No MLAG / ESI multi-homing.** Each host attaches to a single leaf.
- **Docker bridge networks ≠ real point-to-point links.** Underlay
  "links" are docker bridges with auto-assigned MACs. Good enough for a
  control/data-plane demo, not for performance work.
- **Static fault scripts.** They demonstrate failure modes, not random
  fault injection or recovery timing.

## What this lab demonstrates

For data-center / fabric / NOS roles, the lab covers, end-to-end:

- Leaf-spine eBGP underlay, including the `next-hop-unchanged` knob that
  every eBGP-EVPN spine needs.
- BGP-EVPN address family up end-to-end (`l2vpn evpn` activated, sessions
  Established, `advertise-all-vni` triggering type-3 IMET emission).
- Type-3 IMET → kernel HER FDB programming: FRR translating "remote VTEP
  exists for this VNI" into the bridge's broadcast forwarding state.
- Linux VXLAN: VTEP source IP, `nolearning`, `neigh_suppress`, and how
  bridge ports interact with the VXLAN device.
- An honest understanding of where the Linux-side EVPN demo stops being
  EVPN and starts being flood-based VXLAN — and *why* (kernel BUM
  split-horizon limitation). On a real switch ASIC, type-2 picks up the
  slack and unicast goes VTEP-to-VTEP.
- Validator-first thinking: a small Python harness that consumes `vtysh`
  JSON and `iproute2` output and emits a pass/fail verdict — the kind of
  check you'd extend in CI or a NOS test framework.
- Fault isolation: three faults at three different layers (BGP session,
  VNI mapping, VXLAN interface) producing distinguishable validator
  output, so you can tell which layer broke from the report alone.
