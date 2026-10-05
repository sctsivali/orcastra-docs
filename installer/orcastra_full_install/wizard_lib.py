"""Helpers for the wizard: smart defaults computed from the live LXD and host state, and
the conflict checks that run on every answer (interactive or not)."""
from __future__ import annotations

import ipaddress
import json
from typing import Dict, List, Optional, Set

from orcastra_core.proc import Proc

from . import topology as T

_DEFAULT_OFFSETS = {"vault": 11, "authentik": 12, "opensearch": 13, "cmp": 14}


def managed_bridges(networks: List[dict]) -> List[dict]:
    out = []
    for n in networks:
        if n.get("managed") and n.get("type") == "bridge":
            addr = (n.get("config") or {}).get("ipv4.address", "")
            if addr and addr not in ("none", "auto"):
                out.append(n)
    return out


def bridge_cidr(network: dict) -> str:
    return (network.get("config") or {}).get("ipv4.address", "")


def used_subnets(proc: Proc, networks: List[dict]) -> List[ipaddress.IPv4Network]:
    """Subnets already routed on the host or owned by an LXD network."""
    nets: List[ipaddress.IPv4Network] = []
    res = proc.run(["ip", "-json", "route"])
    if res.ok and res.out.strip():
        for r in json.loads(res.out):
            dst = r.get("dst", "")
            if dst and dst != "default":
                try:
                    nets.append(ipaddress.ip_network(dst if "/" in dst else dst + "/32", strict=False))
                except ValueError:
                    pass
    for n in networks:
        cidr = bridge_cidr(n)
        if "/" in cidr:
            nets.append(ipaddress.ip_interface(cidr).network)
    return nets


def free_subnet(proc: Proc, networks: List[dict]) -> str:
    """First 10.77-10.99 /24 that overlaps nothing on the host and is outside the Docker
    pool used inside the instances."""
    taken = used_subnets(proc, networks) + [ipaddress.ip_network(T.DOCKER_POOL)]
    for third in range(77, 100):
        cand = ipaddress.ip_network(f"10.{third}.0.0/24")
        if not any(cand.overlaps(t) for t in taken):
            return f"10.{third}.0.1/24"
    return "10.77.0.1/24"


def subnet_conflict(proc: Proc, networks: List[dict], cidr: str) -> Optional[str]:
    net = ipaddress.ip_interface(cidr).network
    if net.overlaps(ipaddress.ip_network(T.DOCKER_POOL)):
        return f"{net} overlaps {T.DOCKER_POOL}, which Docker uses inside the instances"
    for t in used_subnets(proc, networks):
        if net.overlaps(t):
            return f"{net} overlaps {t}, which is already in use on this host"
    return None


def default_ip(cidr: str, role: str) -> str:
    net = ipaddress.ip_interface(cidr).network
    return str(net.network_address + _DEFAULT_OFFSETS[role])


def lease_owners(leases: List[dict]) -> Dict[str, str]:
    """IP -> hostname of the instance (or host) holding it, from LXD's lease table."""
    out = {}
    for l in leases:
        addr = l.get("address", "")
        if addr and ":" not in addr:
            out[addr] = f"{l.get('project', 'default')}/{l.get('hostname', '?')}"
    return out


def ip_taken(owners: Dict[str, str], ip: str, project: str) -> Optional[str]:
    """Name of whoever holds `ip`, unless it is our own instance (re-run)."""
    owner = owners.get(ip)
    if not owner:
        return None
    proj, _, host = owner.partition("/")
    if proj == project and host.startswith(T.PREFIX):
        return None
    return owner


def answers_ping(proc: Proc, ip: str) -> bool:
    return proc.run(["ping", "-c", "1", "-W", "1", ip], timeout=5).ok


def forward_port_owner(forwards: List[dict], listen: str, port: int) -> Optional[str]:
    """Target of an existing forward on listen:port, if any (another workload's port)."""
    for f in forwards:
        if f.get("listen_address") != listen:
            continue
        if (f.get("config") or {}).get("target_address"):
            return f"{f['network']} default target {f['config']['target_address']}"
        for p in f.get("ports") or []:
            if p.get("protocol") == "tcp" and port in expand_ports(p.get("listen_port", "")):
                return f"{f['network']} -> {p.get('target_address')}:{p.get('target_port') or port}"
    return None


def expand_ports(spec: str) -> Set[int]:
    out: Set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out


def listen_on_other_network(forwards: List[dict], listen: str, network: str) -> Optional[str]:
    for f in forwards:
        if f.get("listen_address") == listen and f.get("network") != network:
            return f["network"]
    return None
