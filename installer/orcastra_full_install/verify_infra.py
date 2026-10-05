"""Infrastructure checks: instances, SSH, the firewall matrix (including Docker-published
ports, now that the containers exist), Vault, OpenSearch and the log pipeline."""
from __future__ import annotations

import time
from typing import Callable, List

from . import opensearch_api as OS
from . import probes
from . import topology as T
from . import vault_api as V
from .httpapi import HttpError

Check = Callable[[str, bool, str], None]


def instances(ctx, check: Check) -> None:
    for role in T.ROLES:
        inst = ctx.lxd.instance(T.name(role)) or {}
        cfg = inst.get("expanded_config") or inst.get("config") or {}
        ok = inst.get("status") == "Running" and cfg.get(T.TAG_KEY) == role
        check(f"{T.name(role)} running", ok, inst.get("status", "missing"))
        dev = (inst.get("expanded_devices") or {}).get("eth0", {})
        check(f"{T.name(role)} address {ctx.ip(role)}", dev.get("ipv4.address") == ctx.ip(role),
              dev.get("ipv4.address", "unset"))


def ssh(ctx, check: Check) -> None:
    conf = "/root/.ssh/config.d/orcastra.conf"
    for role in T.ROLES:
        res = ctx.proc.run(["ssh", "-F", conf, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                            T.name(role), "true"], timeout=30)
        check(f"ssh {T.name(role)}", res.ok, res.err.strip()[-120:])


def firewall(ctx, check: Check) -> None:
    ip = {r: ctx.ip(r) for r in T.ROLES}
    hub = ctx.remote

    def expect(label: str, outcome: str, allowed: bool) -> None:
        check(label, probes.allowed(outcome) == allowed,
              f"{outcome}, {'allowed' if allowed else 'blocked'} expected")

    expect("vault -> opensearch:9200", probes.from_instance(hub("vault"), ip["opensearch"], 9200), True)
    expect("cmp -> opensearch:9200", probes.from_instance(hub("cmp"), ip["opensearch"], 9200), True)
    expect("authentik -> opensearch:9200",
           probes.from_instance(hub("authentik"), ip["opensearch"], 9200), False)
    expect("cmp -> vault:8200", probes.from_instance(hub("cmp"), ip["vault"], 8200), True)
    expect("opensearch -> vault:8200", probes.from_instance(hub("opensearch"), ip["vault"], 8200), False)
    expect("host -> cmp:5432 (Postgres)", probes.from_host(ip["cmp"], 5432), False)
    expect("host -> cmp:6381 (Redis)", probes.from_host(ip["cmp"], 6381), False)
    expect("authentik -> cmp:22", probes.from_instance(hub("authentik"), ip["cmp"], 22), False)
    # a container published on 0.0.0.0 that is on no allow-list must be unreachable
    ver = ctx.values["CMP_VERSION"]
    r = hub("cmp")
    r.run("docker rm -f orcastra-fwprobe >/dev/null 2>&1 || true; "
          f"docker run -d --rm --name orcastra-fwprobe -p 0.0.0.0:18080:8000 --entrypoint python "
          f"svlct/orcastra-dashboard:backend-{ver} -m http.server 8000 >/dev/null; sleep 3",
          check=False, timeout=120)
    try:
        local = r.run("timeout 4 bash -c '</dev/tcp/127.0.0.1/18080' && echo open || echo closed",
                      check=False).out.strip()
        if local.endswith("open"):
            expect("host -> cmp:18080 (unlisted published port)", probes.from_host(ip["cmp"], 18080), False)
            expect("authentik -> cmp:18080 (unlisted published port)",
                   probes.from_instance(hub("authentik"), ip["cmp"], 18080), False)
        else:
            check("published-port probe container", False, "could not start the probe container")
    finally:
        r.run("docker rm -f orcastra-fwprobe >/dev/null 2>&1 || true", check=False)


def vault(ctx, check: Check) -> None:
    st = V.seal_status(ctx)
    check("Vault initialized and unsealed", st.get("initialized") and not st.get("sealed"),
          f"sealed={st.get('sealed')} type={st.get('storage_type')}")
    check("Vault storage is raft", st.get("storage_type") == "raft", str(st.get("storage_type")))
    info = V.lookup_self(ctx, ctx.secrets.get("vault_dashboard_token"))
    check("dashboard token periodic, renewable, non-root",
          bool(info.get("period")) and info.get("renewable") and "root" not in info.get("policies", []),
          f"policies={info.get('policies')} ttl={int(info.get('ttl') or 0) // 3600}h")


def _health(ctx, wait: int = 180) -> dict:
    """OpenSearch answers 503 for a minute or two after a boot while its security index
    loads, so give it that long before calling it down."""
    deadline = time.monotonic() + wait
    while True:
        try:
            return OS.health(ctx)
        except HttpError as exc:
            if time.monotonic() > deadline:
                return {"status": f"unreachable (HTTP {exc.status})"}
        time.sleep(10)


def opensearch(ctx, check: Check) -> None:
    h = _health(ctx)
    if not h.get("number_of_nodes"):
        check("OpenSearch answers over TLS", False, str(h.get("status")))
        return
    check("OpenSearch health green, no unassigned shards (TLS verified by the private CA)",
          h.get("status") == "green" and not h.get("unassigned_shards"),
          f"{h.get('status')}, unassigned={h.get('unassigned_shards')}")
    pol = set(OS.ism_policies(ctx))
    check("5 ISM policies from the guide", set(OS.POLICIES) <= pol, ", ".join(sorted(pol)))
    check("dashboards imported", OS.count_dashboards(ctx) >= 4, str(OS.count_dashboards(ctx)))
    demo = ctx.remote("opensearch").run("docker exec opensearch sh -c 'ls config/esnode.pem config/kirk.pem 2>/dev/null' || true",
                                        check=False).out.strip()
    check("no demo certificates in the OpenSearch container", demo == "", demo or "none")


def logs(ctx, check: Check, wait: int = 240) -> None:
    """Both forwarders deliver: Vault audit events and CMP container logs reach indices."""
    deadline = time.monotonic() + wait
    vault_n = cmp_n = 0
    while time.monotonic() < deadline:
        vault_n = sum(OS.index_counts(ctx, "vault-audit-*").values())
        cmp_n = sum(OS.index_counts(ctx, "orcastra-*").values())
        if vault_n and cmp_n:
            break
        time.sleep(15)
    check("Vault audit log reaches OpenSearch (vault-audit-*)", vault_n > 0, f"{vault_n} documents")
    check("CMP logs reach OpenSearch (orcastra-*)", cmp_n > 0, f"{cmp_n} documents")


def all_checks(ctx, check: Check) -> List[str]:
    for fn in (instances, ssh, firewall, vault, opensearch):
        fn(ctx, check)
    return []
