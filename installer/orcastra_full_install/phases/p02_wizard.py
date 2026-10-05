"""Phase 2 - Configuration wizard. Every answer is checked against the live host and LXD
state (pools, bridges, leases, listeners, forwards) whether it was typed, read from the
answer file, or defaulted. Ends with a resource check and a summary to confirm.

On a re-run the previous answers are the defaults, and settings that the existing
instances were built with cannot change (see config.FROZEN)."""
from __future__ import annotations

import ipaddress

from orcastra_core.errors import AbortByUser, ConfigError

from .. import config as C
from .. import hostnet, wizard_lib as W
from .. import topology as T

TITLE = "Configuration"


def _ask(ctx, key: str, question: str, default=None, validate=None) -> str:
    """A value the operator settled (flag, answer file, environment, previous run) is used
    after validation. Anything else is asked in an interactive run, with the default
    offered, and taken from the default in an unattended one."""
    settled = key in ctx.answered or not ctx.interactive
    if not settled and ctx.values.get(key):
        default = ctx.values[key]
    if settled and key in ctx.values and ctx.values[key] != "":
        err = validate(ctx.values[key]) if validate else None
        if err:
            if not ctx.interactive:
                raise ConfigError(f"{key}={ctx.values[key]!r}: {err}")
            ctx.log.warn(f"{key}: {err}")
        else:
            ctx.log.detail(f"{question}: {ctx.values[key]}")
            return ctx.values[key]
    ans = ctx.prompt.ask(question, default=default, key=key.lower().replace("_", "-"),
                         validate=validate)
    ctx.values[key] = ans
    ctx.answered.add(key)
    return ans


def _instance_type(ctx) -> None:
    def check(v: str):
        err = C.v_choice("vm", "container")(v)
        if err:
            return err
        if v == "vm" and not ctx.facts.get("kvm"):
            return "this host has no /dev/kvm, so only containers are possible"
        return None
    default = "vm" if ctx.facts.get("kvm") else "container"
    _ask(ctx, "INSTANCE_TYPE", "Instance type (vm or container)", default, check)


def _pool(ctx) -> None:
    pools = ctx.lxd.pools()
    names = [p["name"] for p in pools]
    if pools:
        ctx.log.info("Storage pools: " + ", ".join(f"{p['name']} ({p.get('driver')})" for p in pools))
    default = "default" if "default" in names else (names[0] if names else "orcastra")
    name = _ask(ctx, "STORAGE_POOL", "Storage pool (existing, or a new name to create)",
                default, C.v_name)
    if name in names:
        ctx.facts["pool_create"] = False
        return
    ctx.facts["pool_create"] = True
    drivers = [d for d in ("zfs", "btrfs", "dir") if d in (ctx.facts.get("lxd_storage_drivers") or ["dir"])]
    _ask(ctx, "POOL_DRIVER", f"Driver for new pool {name}", drivers[0] if drivers else "dir",
         C.v_choice(*(drivers or ["dir"])))
    if ctx.values["POOL_DRIVER"] != "dir" and not ctx.values.get("POOL_SOURCE"):
        need = sum(s["disk"] for s in ctx.sizes.values()) + 20
        _ask(ctx, "POOL_SIZE_GIB", f"Size of the loop file for {name} in GiB", str(need),
             C.v_int_range(30, 100000))


def _network(ctx) -> None:
    nets = ctx.lxd.networks()
    bridges = W.managed_bridges(nets)
    names = [n["name"] for n in bridges]
    if bridges:
        ctx.log.info("Managed bridges: " + ", ".join(f"{n['name']} ({W.bridge_cidr(n)})" for n in bridges))
    # a bridge of its own by default: the platform's firewalls trust source addresses, and a
    # shared bridge puts unrelated workloads next to Vault
    default = "orcastrabr0"
    name = _ask(ctx, "NETWORK", "Network (a new name creates a dedicated bridge, or pick an "
                "existing managed bridge)", default, C.v_name)
    if name in names:
        cidr = W.bridge_cidr(next(n for n in bridges if n["name"] == name))
        ctx.facts["network_create"] = False
        ctx.values["NETWORK_SUBNET"] = cidr
    else:
        if any(n["name"] == name for n in nets):
            raise ConfigError(f"Network {name} exists but is not a managed bridge with IPv4.",
                              remediation="Pick a managed bridge or a new name.")
        ctx.facts["network_create"] = True

        def check(v: str):
            return C.v_subnet(v) or W.subnet_conflict(ctx.proc, nets, v)
        cidr = _ask(ctx, "NETWORK_SUBNET", f"Gateway/prefix for new bridge {name}",
                    W.free_subnet(ctx.proc, nets), check)
    ctx.facts["bridge_gateway"] = cidr.split("/")[0]


def _ips(ctx) -> None:
    cidr = ctx.values["NETWORK_SUBNET"]
    gw = cidr.split("/")[0]
    owners = {} if ctx.facts.get("network_create") else W.lease_owners(ctx.lxd.leases(ctx.values["NETWORK"]))
    chosen = {}
    for role in T.ROLES:
        key = f"IP_{role.upper()}"
        in_net = C.v_ip_in(cidr, gw)

        def check(v: str, _role=role):
            err = in_net(v)
            if err:
                return err
            if v in chosen.values():
                return f"{v} is already used for another instance"
            owner = W.ip_taken(owners, v, ctx.project)
            if owner:
                return f"{v} is leased to {owner}"
            inst = ctx.lxd.instance(T.name(_role))
            if not owner and inst is None and not ctx.facts.get("network_create") \
                    and W.answers_ping(ctx.proc, v):
                return f"{v} answers ping, something already uses it"
            return None
        chosen[role] = _ask(ctx, key, f"Private IPv4 for {T.name(role)}", W.default_ip(cidr, role), check)


def _sizing(ctx) -> None:
    _ask(ctx, "SIZING", "Sizing (compact, production or custom)", "compact",
         C.v_choice("compact", "production", "custom"))
    if ctx.values["SIZING"] == "custom":
        base = T.SIZING["compact"]
        for role in T.ROLES:
            for k, field, hi in (("cpu", "CPU", 64), ("mem", "MEM", 512), ("disk", "DISK", 4096)):
                lo = T.MIN_SIZE[role][k]
                _ask(ctx, f"{field}_{role.upper()}", f"{T.name(role)} {k} ({'GiB' if k != 'cpu' else 'vCPU'})",
                     str(base[role][k]), C.v_int_range(lo, hi))
    for role, s in ctx.sizes.items():
        for k in ("cpu", "mem", "disk"):
            if s[k] < T.MIN_SIZE[role][k]:
                raise ConfigError(f"{T.name(role)} {k}={s[k]} is below the minimum {T.MIN_SIZE[role][k]}.")


def _host_address(ctx) -> None:
    lxd_bridges = {n["name"] for n in ctx.lxd.networks() if n.get("managed")}
    addrs = [a for a in hostnet.host_addresses(ctx.proc) if a["kind"] == "host"]
    # plain host interfaces first, LXD bridge gateways (reachable only from their bridge
    # or routed networks) after them
    addrs.sort(key=lambda a: a["ifname"] in lxd_bridges)
    if addrs:
        ctx.log.info("Host addresses: " + ", ".join(
            f"{a['ip']} ({a['ifname']}{', LXD bridge' if a['ifname'] in lxd_bridges else ''})"
            for a in addrs))
    host_ips = {a["ip"] for a in addrs}
    net = ipaddress.ip_interface(ctx.values["NETWORK_SUBNET"]).network

    def check(v: str):
        err = C.v_ipv4(v)
        if err:
            return err
        if ipaddress.ip_address(v) in net:
            return f"{v} is inside the instance bridge {net}, pick an address users can reach"
        if host_ips and v not in host_ips:
            return f"{v} is not configured on this host ({', '.join(sorted(host_ips))})"
        return None
    ip = _ask(ctx, "HOST_ADDRESS", "Host address browsers will use",
              addrs[0]["ip"] if addrs else None, check)
    if any(a["ip"] == ip and a["dynamic"] == "yes" for a in addrs):
        ctx.log.warn(f"{ip} comes from DHCP. The login URLs are tied to it, so reserve it "
                     "on your DHCP server or use a static address.")


def _ports(ctx) -> None:
    listen = ctx.values["HOST_ADDRESS"]
    listeners = hostnet.listening_ports(ctx.proc)
    forwards = ctx.lxd.all_forwards()
    other = W.listen_on_other_network(forwards, listen, ctx.values["NETWORK"])
    if other:
        raise ConfigError(f"{listen} already has port forwards on network {other}.",
                          remediation=f"Use network {other} for the instances, or another host address.")
    owned = set((ctx.state.artifact("forward_ports") or {}).get("ports", []))
    chosen = []
    for svc in T.PUBLIC_PORTS:
        key = f"PORT_{svc.upper()}"

        def check(v: str):
            err = C.v_port(v)
            if err:
                return err
            p = int(v)
            if p in chosen:
                return f"port {p} is already used for another service"
            if p in owned:
                return None
            if hostnet.port_busy_on(listeners, p, listen):
                return f"something on this host already listens on {listen}:{p}"
            owner = W.forward_port_owner(forwards, listen, p)
            if owner:
                return f"{listen}:{p} is already forwarded ({owner})"
            return None
        chosen.append(int(_ask(ctx, key, f"Host port for {svc}", C.FIELDS[key][0], check)))


def _admin(ctx) -> None:
    _ask(ctx, "ADMIN_EMAIL", "Admin email (Authentik akadmin)", None, C.v_email)
    if ctx.values.get("ADMIN_PASSWORD"):
        err = C.v_password(ctx.values["ADMIN_PASSWORD"])
        if err:
            raise ConfigError(f"ADMIN_PASSWORD: {err}")
        return
    if ctx.secrets.maybe("admin_password"):
        return
    if ctx.interactive and ctx.prompt.confirm("Set the akadmin password yourself? (No = generate one)",
                                              default=False):
        ctx.values["ADMIN_PASSWORD"] = ctx.prompt.ask_secret("akadmin password", validate=C.v_password)


def _version(ctx) -> None:
    """Pick the CMP release (default: the newest this installer ships). A newer release
    on Docker Hub that this installer does not know yet is reported, not guessed at."""
    from .. import registry
    published = registry.published_versions() or []
    unknown_newer = []
    for v in published:
        if v in T.CMP_RELEASES:
            break
        unknown_newer.append(v)
    if unknown_newer:
        ctx.log.warn(f"Release {unknown_newer[0]} is published but this installer only knows up to "
                     f"{T.CMP_LATEST}. Download the newest installer to deploy it.")
    if "CMP_VERSION" not in ctx.values or ctx.values["CMP_VERSION"] in ("", "latest"):
        ctx.log.info("Available releases: " + ", ".join(
            f"{v}{' (latest)' if v == T.CMP_LATEST else ''}" for v in T.CMP_RELEASES))
    choice = _ask(ctx, "CMP_VERSION", "Orcastra CMP version (latest or a release above)",
                  "latest", C.v_version)
    ctx.values["CMP_VERSION"] = T.resolve_version(choice)
    if choice == "latest":
        ctx.log.detail(f"latest -> {ctx.values['CMP_VERSION']}")


def _summary(ctx) -> None:
    s = ctx.sizes
    ctx.log.info("")
    ctx.log.info(f"Instances ({ctx.values['INSTANCE_TYPE']}, project {ctx.project}, "
                 f"pool {ctx.values['STORAGE_POOL']}, network {ctx.values['NETWORK']} "
                 f"{ctx.values['NETWORK_SUBNET']}):")
    if ctx.facts.get("pool_create") and ctx.values.get("POOL_SOURCE"):
        ctx.log.warn(f"The new pool will be created on {ctx.values['POOL_SOURCE']}. LXD formats a "
                     "block device given here, erasing what is on it.")
    for role in T.ROLES:
        ctx.log.info(f"  {T.name(role):18} {ctx.ip(role):15} {s[role]['cpu']} vCPU / "
                     f"{s[role]['mem']} GiB / {s[role]['disk']} GiB")
    ctx.log.info("Browser URLs (plain HTTP):")
    for svc, label in (("cmp", "Orcastra CMP"), ("authentik", "Authentik"), ("api", "CMP API"),
                       ("logs", "OpenSearch Dashboards")):
        ctx.log.info(f"  {label:22} {ctx.public_url(svc)}")
    ctx.log.info(f"CMP version {ctx.values['CMP_VERSION']}, admin {ctx.values['ADMIN_EMAIL']}")
    ctx.log.warn("These URLs are plain HTTP. Keep them on a trusted network or VPN, "
                 "do not expose them to the internet as they are.")
    if not ctx.prompt.confirm("Proceed with this configuration?", default=True):
        raise AbortByUser("Stopped at the configuration summary.")


def _static_checks(ctx) -> None:
    """Values that never get a question of their own still get validated."""
    for key, check in (("LXD_PROJECT", C.v_name), ("IMAGE", C.v_image),
                       ("POOL_SOURCE", C.v_source), ("FIX_HOST_FIREWALL", C.v_choice("yes", "no", "ask"))):
        value = ctx.values.get(key, "")
        err = check(value) if value else None
        if err:
            raise ConfigError(f"{key}: {err}")


def run(ctx) -> None:
    _static_checks(ctx)
    previous = ctx.state.data.get("config") or {}
    built = bool(ctx.state.artifact("instances"))
    if built:
        # cli.py already filled non-explicit keys from the previous run, so anything that
        # still differs was asked for explicitly ("latest" is compared as what it means)
        if "CMP_VERSION" in ctx.values:
            ctx.values["CMP_VERSION"] = T.resolve_version(ctx.values["CMP_VERSION"])
        changed = C.frozen_changes(previous, ctx.values)
        try:
            resized = C.sizes_from(previous) != C.sizes_from(ctx.values)
        except ConfigError:
            resized = True
        if resized:
            raise ConfigError(
                "The instance sizes cannot be changed by re-running the installer.",
                remediation="Resize an instance with `lxc config set --project "
                            f"{ctx.project} <instance> limits.cpu=N limits.memory=NGiB` (and "
                            "`lxc config device set ... root size=NGiB` to grow its disk), then "
                            "re-run with the original sizing answers.")
        if changed:
            raise ConfigError(
                "These settings cannot change once instances exist: " + ", ".join(
                    f"{k} ({previous[k]} -> {ctx.values[k]})" for k in changed),
                remediation="Run `orcastra-full uninstall` first to rebuild with new values.")

    _instance_type(ctx)
    _pool(ctx)
    _network(ctx)
    _ips(ctx)
    _sizing(ctx)
    _host_address(ctx)
    _ports(ctx)
    _admin(ctx)
    C.check_unique(ctx.values, [f"IP_{r.upper()}" for r in T.ROLES], "IP address")
    _version(ctx)

    from ..plancheck import check_plan
    check_plan(ctx)
    _summary(ctx)

    rerender = [k for k in C.RERENDER if previous.get(k) not in (None, ctx.values.get(k))]
    if built and rerender:
        ctx.log.info("Changed: " + ", ".join(rerender) + ". Dependent phases will re-run.")
        ctx.state.reset_phases(["firewall", "authentik", "cmp", "exposure", "verify", "summary"])
    persisted = {k: v for k, v in ctx.values.items() if k != "ADMIN_PASSWORD"}
    ctx.state.set_config(persisted)
