"""Low-level checks against the lab containers. Each function returns
(ok: bool, detail: str) so validate_overlay.py can print a uniform report.
"""

import json
import subprocess


def _run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def container_running(name):
    rc, out, _ = _run(f"docker inspect -f '{{{{.State.Running}}}}' {name}")
    if rc != 0:
        return False, "not found"
    return out.strip() == "true", out.strip()


def bgp_underlay_established(container, peer_ip):
    rc, out, err = _run(
        f"docker exec {container} vtysh -c 'show ip bgp summary json'"
    )
    if rc != 0:
        return False, f"vtysh failed: {err.strip()[:60]}"
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return False, "bad json from vtysh"
    peers = data.get("ipv4Unicast", {}).get("peers", {})
    p = peers.get(peer_ip)
    if not p:
        return False, f"peer {peer_ip} not configured"
    state = p.get("state", "?")
    return state == "Established", f"{peer_ip}={state}"


def evpn_imet_from_peer(container, peer_vtep):
    """Require a type-3 IMET route in BGP EVPN whose [3] prefix carries the
    remote VTEP IP (i.e. we actually heard the peer announce itself for the
    VNI). Counting type-2 separately is informational; this lab does not
    exercise type-2 (see README Limitations), so pass/fail is keyed on the
    presence of the peer's IMET specifically."""
    rc, out, err = _run(
        f"docker exec {container} vtysh -c 'show bgp l2vpn evpn json'"
    )
    if rc != 0:
        return False, f"vtysh failed: {err.strip()[:60]}"
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return False, "bad json"
    n_type2 = 0
    n_type3 = 0
    peer_imet_seen = False
    needle = f"[3]:[0]:[32]:[{peer_vtep}]"
    for rd_block in data.values():
        if not isinstance(rd_block, dict):
            continue
        for prefix in rd_block:
            if prefix.startswith("[2]"):
                n_type2 += 1
            elif prefix.startswith("[3]"):
                n_type3 += 1
                if needle in prefix:
                    peer_imet_seen = True
    if not peer_imet_seen:
        return False, (
            f"no type-3 IMET from peer {peer_vtep} "
            f"(have type-2={n_type2} type-3={n_type3})"
        )
    return True, f"peer-IMET=yes type-2={n_type2} type-3={n_type3}"


def vxlan_iface_exists(container, iface="vni10010", expected_vni=10010):
    rc, out, _ = _run(f"docker exec {container} ip -d link show {iface}")
    if rc != 0:
        return False, "missing"
    if f"vxlan id {expected_vni} " not in out and f"vxlan id {expected_vni}\n" not in out:
        return False, f"wrong VNI (expected {expected_vni})"
    return True, f"vni={expected_vni}"


def bridge_fdb_has_her_to(container, peer_vtep, iface="vni10010"):
    """Require at least one head-end-replication FDB entry on `iface` whose
    dst is `peer_vtep` -- i.e. FRR has actually programmed the kernel to
    flood BUM toward this peer. Previously this check passed on any FDB
    state, which masked the case where the peer's IMET wasn't installed."""
    rc, out, _ = _run(f"docker exec {container} bridge fdb show dev {iface}")
    if rc != 0:
        return False, "bridge cmd failed"
    entries = [l for l in out.splitlines() if l.strip()]
    if not entries:
        return False, "empty fdb"
    remote_to_peer = [
        l for l in entries
        if f"dst {peer_vtep}" in l and l.lower().startswith("00:00:00:00:00:00")
    ]
    if not remote_to_peer:
        remote_any = [l for l in entries if " dst " in l or l.startswith("00:")]
        return False, (
            f"no HER entry to {peer_vtep} "
            f"(entries={len(entries)} remote-any={len(remote_any)})"
        )
    return True, f"HER->{peer_vtep} present (entries={len(entries)})"


def host_can_ping(src, dst_ip, count=15, timeout=3, min_replies=4):
    # Without hardware-style EVPN split-horizon, the BUM path on Docker
    # Desktop's kernel is slow and the first couple of ICMP can drop while
    # ARP resolves. Flush the neigh cache first (otherwise a stale FAILED
    # entry from an earlier validator run will make `ping` give up before
    # ARP is even attempted), then enforce a minimum-reply floor so a
    # totally-broken data plane (0/N) fails while a working-but-lossy one
    # (which is the realistic regime here -- see README) still passes.
    # The floor is well below the observed reply rate, not a majority.
    _run(f"docker exec {src} ip neigh flush all")
    _, out, _ = _run(f"docker exec {src} ping -c {count} -W {timeout} {dst_ip}")
    received = 0
    for line in out.splitlines():
        if "packets transmitted" in line:
            for tok in line.split(","):
                if "received" in tok:
                    try:
                        received = int(tok.strip().split()[0])
                    except ValueError:
                        pass
    ok = received >= min_replies
    return ok, f"{received}/{count} replies (need >={min_replies})"


def loopback_reachable(container, target, source=None):
    # VTEP-to-VTEP reachability has to source from the loopback: the underlay
    # only advertises /32 loopbacks, not the P2P /24s, so an unsourced ping
    # would have no return route.
    src_flag = f"-I {source} " if source else ""
    rc, _, _ = _run(f"docker exec {container} ping -c 1 -W 2 {src_flag}{target}")
    return rc == 0, "reachable" if rc == 0 else "unreachable"
