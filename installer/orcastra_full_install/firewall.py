"""Per-instance nftables rules and the CMP hairpin NAT, rendered as one file loaded by a
systemd unit before Docker starts.

Design notes (each one prevents a real failure):
- Own tables, never `flush ruleset`, so Docker's iptables-nft tables stay intact.
- `policy accept` plus explicit drops of NEW inbound tcp/udp on the guest interface, so
  replies, DHCP, ICMP/ND and container egress keep working.
- Ports Docker publishes are DNAT'ed before filtering, so they are matched in the forward
  hook by their ORIGINAL destination port (`ct original proto-dst`).
- Only the instance's own internal interfaces are trusted (loopback and Docker's bridges and
  veths). Every other interface, whatever it is called (enp5s0 in a VM, eth0 in a container,
  or a NIC renamed later), gets the default-deny, so the rules fail closed.
- The file starts with `table X` + `delete table X`, which makes `nft -f` idempotent.
- Docker and Vault require the firewall unit (drop-ins), so they never start unfiltered.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from . import topology as T

# (port, allowed sources) - "any" means every source, used for the public forward targets
Rule = Tuple[int, Sequence[str]]


def rules_for(role: str, ips: Dict[str, str], host_bridge_ip: str) -> Dict[str, List[Rule]]:
    """{'input': [...], 'forward': [...]} allow-lists for one instance."""
    mgmt = [host_bridge_ip]
    inp: List[Rule] = [(22, mgmt)]
    fwd: List[Rule] = []
    if role == "vault":
        inp.append((8200, [ips["cmp"]] + mgmt))
    elif role == "authentik":
        fwd.append((9000, ["any"]))
    elif role == "opensearch":
        fwd.append((9200, [ips["vault"], ips["cmp"]] + mgmt))
        fwd.append((5601, ["any"]))
    elif role == "cmp":
        fwd.append((4321, ["any"]))
        fwd.append((8765, ["any"]))
    return {"input": inp, "forward": fwd}


def _allow(port: int, sources: Sequence[str], *, forward: bool) -> str:
    match = f"ct status dnat ct original proto-dst {port}" if forward else f"tcp dport {port}"
    if "any" in sources:
        return f"    meta l4proto tcp {match} accept"
    srcs = ", ".join(sorted(set(sources)))
    return f"    meta l4proto tcp {match} ip saddr {{ {srcs} }} accept"


INTERNAL = ('    iifname { "lo", "docker0" } accept\n'
            '    iifname "br-*" accept\n'
            '    iifname "veth*" accept')


def render(role: str, ips: Dict[str, str], host_bridge_ip: str,
           hairpin: Dict[str, object] = None) -> str:
    """The complete nft file for one instance. `hairpin` (cmp only): {'listen': host
    address, 'port': host port of Authentik, 'target': authentik ip}."""
    rules = rules_for(role, ips, host_bridge_ip)
    lines = [
        f"# Managed by orcastra-full ({T.name(role)}). Re-run the installer to change it.",
        "table inet orcastra_fw",
        "delete table inet orcastra_fw",
        "table inet orcastra_fw {",
        "  chain input {",
        "    type filter hook input priority filter; policy accept;",
        INTERNAL,
        "    ct state established,related accept",
        "    meta l4proto { icmp, ipv6-icmp } accept",
        "    udp sport 67 udp dport 68 accept",
        "    udp sport 547 udp dport 546 accept",
    ]
    lines += [_allow(p, s, forward=False) for p, s in rules["input"]]
    lines += [
        "    meta l4proto { tcp, udp } ct state new counter drop",
        "  }",
        "  chain forward {",
        "    type filter hook forward priority filter; policy accept;",
        INTERNAL,
        "    ct state established,related accept",
    ]
    lines += [_allow(p, s, forward=True) for p, s in rules["forward"]]
    lines += [
        "    meta l4proto { tcp, udp } ct state new counter drop",
        "  }",
        "}",
    ]
    if hairpin:
        dnat = (f"ip daddr {hairpin['listen']} tcp dport {hairpin['port']} "
                f"dnat to {hairpin['target']}:9000")
        lines += [
            "table ip orcastra_nat",
            "delete table ip orcastra_nat",
            "table ip orcastra_nat {",
            "  chain prerouting {",
            "    type nat hook prerouting priority dstnat - 5; policy accept;",
            f"    {dnat}",
            "  }",
            "  chain output {",
            "    type nat hook output priority -105; policy accept;",
            f"    {dnat}",
            "  }",
            "}",
        ]
    return "\n".join(lines) + "\n"


UNIT = """[Unit]
Description=Orcastra instance firewall and NAT rules
DefaultDependencies=no
Wants=network-pre.target
Before=network-pre.target docker.service vault.service
After=local-fs.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/orcastra/firewall.nft
ExecStop=/bin/sh -c '/usr/sbin/nft delete table inet orcastra_fw 2>/dev/null; /usr/sbin/nft delete table ip orcastra_nat 2>/dev/null; true'

[Install]
WantedBy=multi-user.target
"""

# Docker and Vault refuse to start when the firewall did not load (fail closed). The unit is
# never restarted by the installer, rules are reloaded with `nft -f`, so these dependencies
# never bounce the services.
REQUIRE_DROPIN = """[Unit]
Requires=orcastra-firewall.service
After=orcastra-firewall.service
"""
