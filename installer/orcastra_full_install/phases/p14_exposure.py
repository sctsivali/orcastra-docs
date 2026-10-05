"""Phase 14 - Browser access through LXD network forwards on the host address
(docs/operations/networking.md). Ports are added to an existing forward on that address
when one exists, and exactly the ports this installer added are recorded, so uninstall never
touches another workload's forwards."""
from __future__ import annotations

from orcastra_core.errors import InstallError

from .. import topology as T

TITLE = "Expose the web endpoints on the host address (LXD forwards)"


def _forward(ctx, net: str, listen: str):
    for f in ctx.lxd.forwards(net):
        if f.get("listen_address") == listen:
            return f
    return None


def remove_ours(ctx, record: dict) -> None:
    """Remove the ports recorded in `record` (and the forward itself if we created it)."""
    if not record:
        return
    net, listen = record["network"], record["listen"]
    for port in record.get("ports", []):
        ctx.lxd.cmd("network", "forward", "port", "remove", net, listen, "tcp", str(port), project=False)
    # a forward this installer created goes only when nothing else added ports to it since
    if record.get("created"):
        fwd = _forward(ctx, net, listen)
        if fwd is not None and not (fwd.get("ports") or []):
            ctx.lxd.cmd("network", "forward", "delete", net, listen, project=False)


def run(ctx) -> None:
    net, listen = ctx.values["NETWORK"], ctx.host_address
    record = ctx.state.artifact("forward_ports") or {}
    if record and (record.get("listen") != listen or record.get("network") != net
                   or sorted(record.get("ports", [])) != sorted(ctx.ports.values())):
        remove_ours(ctx, record)
        ctx.log.info(f"Removed the previous forwards on {record['listen']}")
        record = {}
        ctx.state.drop_artifact("forward_ports")

    fwd = _forward(ctx, net, listen)
    created = bool(record.get("created"))
    if fwd is None:
        # the description is not a config key, it goes in the YAML document on stdin
        ctx.lxd.must("network", "forward", "create", net, listen, project=False,
                     input="description: Orcastra CMP (managed by orcastra-full)\n",
                     what=f"create forward {listen}")
        created = True
        fwd = _forward(ctx, net, listen) or {"ports": []}
    record = {"network": net, "listen": listen, "created": created, "ports": record.get("ports", [])}
    ctx.state.set_artifact("forward_ports", record)

    existing = {}
    for p in fwd.get("ports") or []:
        if p.get("protocol") == "tcp":
            existing[str(p.get("listen_port"))] = (p.get("target_address"), str(p.get("target_port") or p.get("listen_port")))
    for svc, (role, target_port) in T.PUBLIC_PORTS.items():
        port = ctx.ports[svc]
        want = (ctx.ip(role), str(target_port))
        have = existing.get(str(port))
        if have == want:
            if port not in record["ports"]:
                ctx.log.info(f"{listen}:{port} was already forwarded to {want[0]}:{want[1]} "
                             "by someone else, leaving it in place")
                continue
        elif have is not None:
            raise InstallError(f"{listen}:{port} is already forwarded to {have[0]}:{have[1]}.",
                               remediation=f"Choose another PORT_{svc.upper()} or remove that forward.")
        else:
            ctx.lxd.must("network", "forward", "port", "add", net, listen, "tcp", str(port),
                         want[0], want[1], project=False, what=f"forward {listen}:{port}")
        if port not in record["ports"]:
            record["ports"].append(port)
            ctx.state.set_artifact("forward_ports", record)
        ctx.log.ok(f"{ctx.public_url(svc)} -> {T.name(role)}:{target_port}")
