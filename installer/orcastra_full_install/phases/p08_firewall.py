"""Phase 8 - Instance firewalls. Applies the allow-lists from firewall.py on every
instance and proves them from the outside: allowed paths must reach the port (open or
refused), denied paths must time out. Ports that Docker will publish are proven later in
the verify phase, once the containers exist."""
from __future__ import annotations

from orcastra_core.errors import InstallError

from .. import firewall, probes
from .. import topology as T

TITLE = "Instance firewalls (nftables)"


def render_for(ctx, role: str) -> str:
    ips = {r: ctx.ip(r) for r in T.ROLES}
    hairpin = None
    if role == "cmp":
        hairpin = {"listen": ctx.host_address, "port": ctx.ports["authentik"],
                   "target": ips["authentik"]}
    return firewall.render(role, ips, ctx.bridge_gateway, hairpin)


def apply(ctx, role: str) -> bool:
    """Write and load the rules. Returns True when they changed."""
    r = ctx.remote(role)
    text = render_for(ctx, role)
    changed = not r.same_content("/etc/orcastra/firewall.nft", text)
    r.run("nft --version >/dev/null 2>&1 || apt-get install -y -q nftables", what="nftables", timeout=600)
    if changed:
        r.write("/etc/orcastra/firewall.nft", text, mode="0600", what="firewall rules")
        r.run("nft -c -f /etc/orcastra/firewall.nft", what="validate firewall rules")
    units = [("/etc/systemd/system/orcastra-firewall.service", firewall.UNIT)]
    service = "vault" if role == "vault" else "docker"
    units.append((f"/etc/systemd/system/{service}.service.d/10-orcastra-firewall.conf",
                  firewall.REQUIRE_DROPIN))
    reload = False
    for path, body in units:
        if not r.same_content(path, body):
            r.write(path, body, what=f"unit {path}")
            reload = True
    if reload:
        r.run("systemctl daemon-reload", what="daemon-reload")
    # load the rules directly: restarting the unit would also restart Docker/Vault (Requires=)
    r.run("nft -f /etc/orcastra/firewall.nft && systemctl enable orcastra-firewall.service "
          ">/dev/null 2>&1 && systemctl start orcastra-firewall.service", what="load firewall rules")
    return changed


def _expect(ctx, label: str, outcome: str, want_allowed: bool, failures: list) -> None:
    good = probes.allowed(outcome) == want_allowed
    (ctx.log.ok if good else ctx.log.error)(
        f"{label}: {outcome} ({'allowed' if want_allowed else 'blocked'} expected)")
    if not good:
        failures.append(label)


def prove_input_paths(ctx) -> None:
    ips = {r: ctx.ip(r) for r in T.ROLES}
    fails: list = []
    for role in T.ROLES:
        _expect(ctx, f"host -> {T.name(role)}:22", probes.from_host(ips[role], 22), True, fails)
    _expect(ctx, "authentik -> vault:22 (no VM-to-VM SSH)",
            probes.from_instance(ctx.remote("authentik"), ips["vault"], 22), False, fails)
    _expect(ctx, "cmp -> vault:8200", probes.from_instance(ctx.remote("cmp"), ips["vault"], 8200),
            True, fails)
    _expect(ctx, "authentik -> vault:8200",
            probes.from_instance(ctx.remote("authentik"), ips["vault"], 8200), False, fails)
    # a listener on a port nobody is allowed to reach must be filtered, not refused
    r = ctx.remote("vault")
    r.run("(timeout 25 python3 -m http.server 18081 --bind 0.0.0.0 >/dev/null 2>&1 &) ; sleep 1",
          what="start probe listener")
    _expect(ctx, "host -> vault:18081 (unlisted port)", probes.from_host(ips["vault"], 18081),
            False, fails)
    _expect(ctx, "cmp -> vault:18081 (unlisted port)",
            probes.from_instance(ctx.remote("cmp"), ips["vault"], 18081), False, fails)
    r.run("pkill -f 'http.server 18081' || true", check=False)
    if fails:
        raise InstallError("Firewall check failed: " + ", ".join(fails),
                           remediation="Inspect `nft list ruleset` in the instances named above.")


def run(ctx) -> None:
    for role in T.ROLES:
        changed = apply(ctx, role)
        ctx.log.ok(f"{T.name(role)}: rules {'loaded' if changed else 'unchanged'}")
    prove_input_paths(ctx)
