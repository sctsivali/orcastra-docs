"""Phase 11 - OpenSearch on orca-opensearch (docs/deployment/vm3-opensearch.md).

Runs before the two log forwarders so the templates and ISM policies exist before the
first daily index is created (ISM only attaches to indices created after the policy)."""
from __future__ import annotations

import re

from orcastra_core.errors import InstallError
from orcastra_core.retry import wait_until

from .. import _blocks, compose, os_render, pki
from .. import opensearch_api as OS
from .. import topology as T

TITLE = "OpenSearch: certificates, security config, pipeline, templates, ISM, dashboards"

DIR = f"{T.REMOTE_DIR}/opensearch"
_HASH = re.compile(r"^\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}$")


def _hash(ctx, key: str, password_key: str) -> str:
    """bcrypt with OpenSearch's own tool (the password goes in on stdin). Stored so every
    re-run renders the same internal_users.yml."""
    stored = ctx.secrets.maybe(key)
    if stored:
        return stored
    out = ctx.remote("opensearch").run(
        f"read -r PW; export PW; docker run --rm -e PW {os_render.IMAGE} "
        "/usr/share/opensearch/plugins/opensearch-security/tools/hash.sh -env PW",
        stdin=ctx.secrets.get(password_key) + "\n", what="bcrypt a password", timeout=300,
        sensitive=True).out
    lines = [l.strip() for l in out.splitlines() if _HASH.match(l.strip())]
    if not lines:
        raise InstallError("OpenSearch hash.sh returned no bcrypt hash: " + out[-300:])
    ctx.secrets.put(key, lines[-1])
    return lines[-1]


def _files(ctx, paths) -> bool:
    s = ctx.secrets
    heap = T.opensearch_heap_gib(ctx.sizes["opensearch"]["mem"])
    hashes = {"admin": _hash(ctx, "os_admin_hash", "os_admin_password"),
              "audit_viewer": _hash(ctx, "os_audit_viewer_hash", "os_audit_viewer_password"),
              "kibanaserver": _hash(ctx, "os_dashboards_hash", "os_dashboards_password")}
    files = [
        ("docker-compose.yml", os_render.compose(heap), "0644", "root:root"),
        (".env", os_render.env(s), "0600", "root:root"),
        ("config/opensearch.yml", os_render.opensearch_yml(), "0600", "1000:1000"),
        ("config/internal_users.yml", os_render.internal_users(hashes), "0600", "1000:1000"),
        ("config/roles.yml", _blocks.OS_ROLES, "0600", "1000:1000"),
        ("config/roles_mapping.yml", _blocks.OS_ROLES_MAPPING, "0600", "1000:1000"),
        ("config/opensearch_dashboards.yml", os_render.dashboards_yml(s.get("os_dashboards_password")),
         "0600", "1000:1000"),
        ("config/certs/node.pem", pki.read(paths["node"]), "0600", "1000:1000"),
        ("config/certs/node-key.pem", pki.read(paths["node_key"]), "0600", "1000:1000"),
        ("config/certs/root-ca.pem", pki.read(paths["ca"]), "0600", "1000:1000"),
    ]
    r = ctx.remote("opensearch")
    for rel, body, mode, owner in files:
        path = f"{DIR}/{rel}"
        if not r.same_content(path, body):
            r.write(path, body, mode=mode, owner=owner)
    return compose.config_hash(body for _, body, _, _ in files)


def run(ctx) -> None:
    r = ctx.remote("opensearch")
    paths = pki.ensure(ctx.pki_dir, ctx.ip("opensearch"), ["opensearch", T.name("opensearch")])
    ctx.log.ok(f"Private CA and node certificate ready (node cert valid until {pki.expiry(paths['node'])})")
    r.run(f"install -d -m 0755 {DIR}/config/certs && install -d -o 1000 -g 1000 -m 0750 "
          "/opt/opensearch/archive", what="opensearch dirs")
    if ctx.is_vm:
        r.run("sysctl -w vm.max_map_count=262144 >/dev/null", what="vm.max_map_count")
    # compose and .env first: the pull needs them, the bcrypt step needs the pulled image
    for rel, body, mode in (("docker-compose.yml", os_render.compose(T.opensearch_heap_gib(
            ctx.sizes["opensearch"]["mem"])), "0644"), (".env", os_render.env(ctx.secrets), "0600")):
        if not r.same_content(f"{DIR}/{rel}", body):
            r.write(f"{DIR}/{rel}", body, mode=mode)
    compose.pull(r, DIR, "opensearch", what="pull OpenSearch images")
    digest = _files(ctx, paths)
    changed = compose.needs_recreate(ctx, "opensearch", digest)
    compose.up(r, DIR, "opensearch", recreate=changed)
    compose.wait_healthy(r, DIR, "opensearch", ["opensearch", "opensearch-dashboards"], timeout=900)
    compose.mark_running(ctx, "opensearch", digest)
    if not wait_until(lambda: OS.health(ctx).get("status") in ("green", "yellow"), timeout=300):
        raise InstallError("OpenSearch cluster health did not reach green/yellow.")
    ctx.log.ok(f"OpenSearch {T.PINS['opensearch']} healthy, TLS verified against the private CA"
               + (" (config changed)" if changed else ""))
    if OS.ensure_fluentbit_user(ctx):
        ctx.log.ok("Created the fluentbit user (backend role log_writer)")
    applied = OS.apply_guide_objects(ctx)
    ctx.log.ok(f"Applied {len(applied)} objects: pipeline, 3 index templates, snapshot repo, 4 ISM policies")
    policies = OS.ism_policies(ctx)
    want = {"orcastra-access-policy", "orcastra-audit-policy", "orcastra-app-policy", "vault-audit-policy"}
    if not want.issubset(policies):
        raise InstallError(f"ISM policies missing: {sorted(want - set(policies))}")
    if not wait_until(lambda: OS.dashboards(ctx).request("GET", "/api/status", ok=(200,))[0] == 200,
                      timeout=300, interval=5):
        raise InstallError("OpenSearch Dashboards API did not answer on :5601.")
    counts = OS.import_dashboards(ctx)
    ctx.log.ok("Imported dashboards: " + ", ".join(f"{k.split('.')[0]} ({v})" for k, v in counts.items()))
