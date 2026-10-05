"""Facts about the LXD host itself: addresses, listening ports, kernel settings, and the
two host firewalls (Docker, ufw) that are known to break LXD bridge forwarding."""
from __future__ import annotations

import ipaddress
import json
import os
import re
from typing import Dict, List, Optional, Set

from orcastra_core.netutil import default_route_ip
from orcastra_core.proc import Proc


def host_addresses(proc: Proc) -> List[Dict[str, str]]:
    """IPv4 addresses on the host with their interface, default-route address first.
    Addresses that belong to LXD/Docker bridges are flagged so the wizard can skip them."""
    res = proc.run(["ip", "-json", "addr"])
    out: List[Dict[str, str]] = []
    if res.ok and res.out.strip():
        for iface in json.loads(res.out):
            ifname = iface.get("ifname", "")
            for a in iface.get("addr_info", []):
                if a.get("family") != "inet":
                    continue
                ip = a.get("local", "")
                addr = ipaddress.ip_address(ip)
                if addr.is_loopback or addr.is_link_local:
                    continue
                kind = "bridge" if re.match(r"^(lxdbr|docker|br-|virbr|incusbr)", ifname) else "host"
                out.append({"ip": ip, "ifname": ifname, "kind": kind,
                            "dynamic": "yes" if a.get("dynamic") else "no"})
    primary = default_route_ip()
    out.sort(key=lambda r: (r["ip"] != primary, r["kind"] != "host"))
    return out


def listening_ports(proc: Proc) -> Dict[int, Set[str]]:
    """TCP port -> set of local addresses something listens on (0.0.0.0 / :: for any)."""
    res = proc.run(["ss", "-H", "-ltn"])
    out: Dict[int, Set[str]] = {}
    if not res.ok:
        return out
    for line in res.out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[3]
        addr, _, port = local.rpartition(":")
        if port.isdigit():
            out.setdefault(int(port), set()).add(addr.strip("[]").split("%")[0])
    return out


def port_busy_on(listeners: Dict[int, Set[str]], port: int, address: str) -> bool:
    addrs = listeners.get(port, set())
    return bool(addrs & {address, "0.0.0.0", "*", "::", ""})


def sysctl(name: str) -> Optional[str]:
    try:
        with open("/proc/sys/" + name.replace(".", "/"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def mem_available_gib() -> float:
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return 0.0


def mem_total_gib() -> float:
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return 0.0


def has_kvm() -> bool:
    return os.path.exists("/dev/kvm")


def docker_forward_drop(proc: Proc) -> bool:
    """True when Docker on the host set the iptables FORWARD policy to DROP, which blocks
    traffic to and from LXD bridges unless DOCKER-USER rules allow it."""
    res = proc.run(["iptables", "-S", "FORWARD"])
    return res.ok and "-P FORWARD DROP" in res.out and "DOCKER" in res.out


def ufw_routed_deny(proc: Proc) -> bool:
    res = proc.run(["ufw", "status", "verbose"])
    if not res.ok or "Status: active" not in res.out:
        return False
    m = re.search(r"Default:.*?(\w+) \(routed\)", res.out)
    return bool(m and m.group(1) in ("deny", "reject"))


def os_release() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    out[k] = v.strip('"')
    except OSError:
        pass
    return out
