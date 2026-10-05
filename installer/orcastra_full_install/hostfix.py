"""Host fixes, applied only with the operator's consent and reverted on uninstall.

- Docker on the host sets the iptables FORWARD policy to DROP, which blocks the bridge.
  LXD's documented fix accepts bridge traffic in DOCKER-USER. Forwarded (DNAT) traffic
  arrives as NEW, so it is accepted too, or the port forwards would not work.
- ufw with a routed "deny" policy has the same effect, and route rules for the bridge fix it.
"""
from __future__ import annotations

import os
from typing import List

from orcastra_core.errors import AbortByUser

from . import hostnet

UNIT_PATH = "/etc/systemd/system/orcastra-host-forward.service"
SCRIPT_PATH = "/usr/local/lib/orcastra/host-forward.sh"

_SCRIPT = """#!/bin/sh
# Managed by orcastra-full: let LXD bridge {br} through Docker's FORWARD DROP policy.
set -e
add() {{ iptables -C DOCKER-USER "$@" 2>/dev/null || iptables -I DOCKER-USER "$@"; }}
del() {{ while iptables -C DOCKER-USER "$@" 2>/dev/null; do iptables -D DOCKER-USER "$@"; done; }}
case "${{1:-add}}" in
  add) add -i {br} -j ACCEPT
       add -o {br} -m conntrack --ctstate RELATED,ESTABLISHED,DNAT -j ACCEPT ;;
  del) del -i {br} -j ACCEPT
       del -o {br} -m conntrack --ctstate RELATED,ESTABLISHED,DNAT -j ACCEPT ;;
esac
"""

_UNIT = """[Unit]
Description=Orcastra: allow LXD bridge traffic through Docker's FORWARD policy
After=docker.service
Wants=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart={script} add
ExecStop={script} del

[Install]
WantedBy=multi-user.target
"""


SYSCTL_PATH = "/etc/sysctl.d/60-orcastra.conf"
MAX_MAP_COUNT = 262144


def _docker_rules_present(ctx, bridge: str) -> bool:
    return all(ctx.proc.run(["iptables", "-C", "DOCKER-USER", *rule]).ok for rule in (
        ["-i", bridge, "-j", "ACCEPT"],
        ["-o", bridge, "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED,DNAT", "-j", "ACCEPT"]))


def _ufw_allows(ctx, bridge: str) -> bool:
    res = ctx.proc.run(["ufw", "status"])
    return res.ok and f"on {bridge}" in res.out


def needed(ctx, bridge: str) -> List[str]:
    """Only what is actually missing: rules the operator already added are left alone and
    are never recorded, so uninstall cannot remove them."""
    out = []
    if hostnet.docker_forward_drop(ctx.proc) and not _docker_rules_present(ctx, bridge):
        out.append("docker")
    if hostnet.ufw_routed_deny(ctx.proc) and not _ufw_allows(ctx, bridge):
        out.append("ufw")
    # containers share the host kernel, and OpenSearch refuses to start below this value
    if not ctx.is_vm and int(hostnet.sysctl("vm.max_map_count") or 0) < MAX_MAP_COUNT:
        out.append("sysctl")
    return out


def _ufw_rules(br: str) -> List[List[str]]:
    return [["allow", "in", "on", br], ["route", "allow", "in", "on", br],
            ["route", "allow", "out", "on", br]]


def apply(ctx, bridge: str) -> List[str]:
    fixes = needed(ctx, bridge)
    if not fixes:
        return []
    what = {"docker": "Docker's FORWARD DROP policy blocks the instance bridge",
            "ufw": "ufw's routed deny policy blocks the instance bridge",
            "sysctl": f"vm.max_map_count is below {MAX_MAP_COUNT}, which OpenSearch needs in a container"}
    for f in fixes:
        ctx.log.warn("Host setting: " + what[f])
    choice = ctx.values.get("FIX_HOST_FIREWALL", "ask")
    ok = choice == "yes" or (choice == "ask" and ctx.prompt.confirm(
        "Apply these host changes (reverted on uninstall)?", default=True))
    if not ok:
        raise AbortByUser("The deployment cannot work with these host settings as they are.",
                          remediation="Allow the bridge yourself (see the LXD docs on Docker and "
                                      f"ufw) and set vm.max_map_count={MAX_MAP_COUNT} for "
                                      "container instances, or re-run with FIX_HOST_FIREWALL=yes.")
    if "docker" in fixes:
        os.makedirs(os.path.dirname(SCRIPT_PATH), exist_ok=True)
        with open(SCRIPT_PATH, "w", encoding="utf-8") as fh:
            fh.write(_SCRIPT.format(br=bridge))
        os.chmod(SCRIPT_PATH, 0o755)
        with open(UNIT_PATH, "w", encoding="utf-8") as fh:
            fh.write(_UNIT.format(script=SCRIPT_PATH))
        ctx.proc.run(["systemctl", "daemon-reload"])
        ctx.proc.run(["systemctl", "enable", "--now", "orcastra-host-forward.service"])
        ctx.log.ok(f"DOCKER-USER now accepts traffic for {bridge} (orcastra-host-forward.service)")
    if "ufw" in fixes:
        for rule in _ufw_rules(bridge):
            ctx.proc.run(["ufw", *rule])
        ctx.log.ok(f"ufw allows and routes traffic on {bridge}")
    if "sysctl" in fixes:
        with open(SYSCTL_PATH, "w", encoding="utf-8") as fh:
            fh.write(f"# Managed by orcastra-full: OpenSearch in an LXD container\n"
                     f"vm.max_map_count = {MAX_MAP_COUNT}\n")
        ctx.proc.run(["sysctl", "-w", f"vm.max_map_count={MAX_MAP_COUNT}"])
        ctx.log.ok(f"vm.max_map_count set to {MAX_MAP_COUNT} ({SYSCTL_PATH})")
    previous = (ctx.state.artifact("host_fixes") or {}).get("fixes", [])
    ctx.state.set_artifact("host_fixes", {"bridge": bridge, "fixes": sorted(set(previous) | set(fixes))})
    return fixes


def revert(ctx) -> None:
    rec = ctx.state.artifact("host_fixes") or {}
    if "docker" in rec.get("fixes", []):
        ctx.proc.run(["systemctl", "disable", "--now", "orcastra-host-forward.service"])
        for p in (UNIT_PATH, SCRIPT_PATH):
            if os.path.exists(p):
                os.unlink(p)
        ctx.proc.run(["systemctl", "daemon-reload"])
    if "ufw" in rec.get("fixes", []):
        for rule in _ufw_rules(rec["bridge"]):
            # `ufw delete <rule>` for plain rules, but `ufw route delete <rule>` for route rules
            argv = ["ufw", "route", "delete", *rule[1:]] if rule[0] == "route" else ["ufw", "delete", *rule]
            ctx.proc.run(argv)
    if "sysctl" in rec.get("fixes", []) and os.path.exists(SYSCTL_PATH):
        os.unlink(SYSCTL_PATH)  # the running value stays until the next reboot
