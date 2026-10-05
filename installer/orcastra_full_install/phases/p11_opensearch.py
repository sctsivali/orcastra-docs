"""Phase 11 - OpenSearch on orca-opensearch, in the order of docs/deployment/vm3-opensearch.md:
certificates (Step 5), compose and config (Steps 6 and 7, with the kibanaserver password in
the Dashboards keystore), start (Step 8), the pipeline and templates (Step 9), the snapshot
repository, ISM policies and zero replicas (Step 10), and only then the fluentbit user
(Step 11), so a forwarder can never write before its index template exists. Dashboards are
imported last (Step 12). The optional Authentik sign-in for Dashboards (Step 13) is not
automated."""
from __future__ import annotations

import re

from orcastra_core.errors import InstallError
from orcastra_core.retry import wait_until

from .. import _blocks, compose, os_render, pki
from .. import opensearch_api as OS
from .. import topology as T

TITLE = "OpenSearch: certificates, security config, templates, ISM, dashboards"

DIR = f"{T.REMOTE_DIR}/opensearch"
KEYSTORE = f"{DIR}/config/opensearch_dashboards.keystore"
_HASH = re.compile(r"^\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}$")

# vm3 Step 7: the password goes in over stdin and lands only in the keystore
_KEYSTORE = r"""
if [ ! -s {ks} ]; then
  tmp="$(mktemp -d)"; chown 1000:1000 "$tmp"
  docker run --rm -i -v "$tmp:/out" --entrypoint bash {image} -c '
    set -e; read -r pw
    bin/opensearch-dashboards-keystore create --silent >/dev/null
    printf %s "$pw" | bin/opensearch-dashboards-keystore add --stdin --silent opensearch.password
    cp config/opensearch_dashboards.keystore /out/'
  install -o 1000 -g 1000 -m 600 "$tmp/opensearch_dashboards.keystore" {ks}
  rm -rf "$tmp"
fi
"""


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
        raise InstallError("OpenSearch hash.sh returned no bcrypt hash.")
    ctx.secrets.put(key, lines[-1])
    return lines[-1]


def _files(ctx, paths) -> str:
    hashes = {"admin": _hash(ctx, "os_admin_hash", "os_admin_password"),
              "audit_viewer": _hash(ctx, "os_audit_viewer_hash", "os_audit_viewer_password"),
              "kibanaserver": _hash(ctx, "os_dashboards_hash", "os_dashboards_password")}
    files = [
        ("config/opensearch.yml", os_render.opensearch_yml(), "0600", "1000:1000"),
        ("config/internal_users.yml", os_render.internal_users(hashes), "0600", "1000:1000"),
        ("config/roles.yml", _blocks.OS_ROLES, "0600", "1000:1000"),
        ("config/roles_mapping.yml", _blocks.OS_ROLES_MAPPING, "0600", "1000:1000"),
        ("config/opensearch_dashboards.yml", os_render.dashboards_yml(), "0600", "1000:1000"),
        ("certs/node.pem", pki.read(paths["node"]), "0644", "1000:1000"),
        ("certs/node-key.pem", pki.read(paths["node_key"]), "0600", "1000:1000"),
        ("certs/root-ca.pem", pki.read(paths["ca"]), "0644", "root:root"),
    ]
    r = ctx.remote("opensearch")
    for rel, body, mode, owner in files:
        if not r.same_content(f"{DIR}/{rel}", body):
            r.write(f"{DIR}/{rel}", body, mode=mode, owner=owner)
    return compose.config_hash(body for _, body, _, _ in files)


def run(ctx) -> None:
    r = ctx.remote("opensearch")
    paths = pki.ensure(ctx.pki_dir, ctx.ip("opensearch"), [ctx.host_address])
    ctx.log.ok(f"Private CA, node and admin certificates ready (node valid until {pki.expiry(paths['node'])})")
    r.run(f"install -d -m 0755 {DIR}/config {DIR}/certs && install -d -o 1000 -g 1000 -m 0750 "
          "/opt/opensearch/archive", what="opensearch dirs")
    if ctx.is_vm:
        r.run("sysctl -w vm.max_map_count=262144 >/dev/null", what="vm.max_map_count")
    # compose and .env first: the pull needs them, the bcrypt and keystore steps need the images
    heap = T.opensearch_heap_gib(ctx.sizes["opensearch"]["mem"])
    base = [("docker-compose.yml", os_render.compose(heap), "0644"),
            (".env", os_render.env(ctx.secrets, ctx.ip("opensearch"), ctx.host_address), "0600")]
    for rel, body, mode in base:
        if not r.same_content(f"{DIR}/{rel}", body):
            r.write(f"{DIR}/{rel}", body, mode=mode)
    compose.pull(r, DIR, "opensearch", what="pull OpenSearch images")
    digest = compose.config_hash([_files(ctx, paths)] + [b for _, b, _ in base])
    r.run(_KEYSTORE.format(ks=KEYSTORE, image=os_render.DASHBOARDS_IMAGE),
          stdin=ctx.secrets.get("os_dashboards_password") + "\n", what="Dashboards keystore",
          timeout=300)
    changed = compose.needs_recreate(ctx, "opensearch", digest)
    compose.up(r, DIR, "opensearch", recreate=changed)
    compose.wait_healthy(r, DIR, "opensearch", ["opensearch", "opensearch-dashboards"], timeout=900)
    compose.mark_running(ctx, "opensearch", digest)
    if not wait_until(lambda: OS.health(ctx).get("status") in ("green", "yellow"), timeout=300):
        raise InstallError("OpenSearch cluster health did not reach green/yellow.")
    ctx.log.ok(f"OpenSearch {T.PINS['opensearch']} healthy, TLS verified against the private CA"
               + (" (containers recreated for new config)" if changed else ""))

    applied = OS.apply_guide_objects(ctx)
    ctx.log.ok(f"Applied {len(applied)} objects from the guide: pipeline, index templates, "
               "snapshot repository, ISM policies, ISM history replicas")
    OS.attach_security_auditlog(ctx)
    missing = set(OS.POLICIES) - set(OS.ism_policies(ctx))
    if missing:
        raise InstallError(f"ISM policies missing: {sorted(missing)}")
    fixed = OS.zero_replicas(ctx)
    if fixed:
        ctx.log.ok("Set 0 replicas on " + ", ".join(fixed))
    if OS.ensure_fluentbit_user(ctx):
        ctx.log.ok("Created the fluentbit user (backend role log_writer), after the templates")
    if not wait_until(lambda: OS.dashboards(ctx).request("GET", "/api/status", ok=(200,))[0] == 200,
                      timeout=300, interval=5):
        raise InstallError("OpenSearch Dashboards API did not answer on :5601.")
    counts = OS.import_dashboards(ctx)
    ctx.log.ok("Imported dashboards: " + ", ".join(f"{k.split('.')[0]} ({v})" for k, v in counts.items()))
