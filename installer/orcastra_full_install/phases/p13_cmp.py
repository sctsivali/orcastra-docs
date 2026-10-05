"""Phase 13 - Orcastra CMP on orca-cmp (docs/deployment/vm4-dashboard.md).

The release compose ships inside the installer (the application repository is private,
so the guide's curl of it fails) and is checked against its published sha256. The hairpin
rule that lets containers reach the Authentik issuer on the host address is already in
place from the firewall phase, so the first OIDC discovery succeeds."""
from __future__ import annotations

from orcastra_core.errors import InstallError

from .. import _blocks, bundled, cmp_render, compose
from .. import topology as T

TITLE = "Orcastra CMP: deploy backend, frontend, Postgres, Redis, Fluent Bit"

DIR = f"{T.REMOTE_DIR}/cmp"
FILE = "docker-compose.prod.yml"
SERVICES = ["postgres", "redis", "backend", "frontend", "fluent-bit"]


def files(ctx):
    ver = ctx.values["CMP_VERSION"]
    comp = bundled.text_sha256(f"cmp/{ver}/{FILE}", T.CMP_COMPOSE_SHA256[ver])
    return [
        (FILE, comp, "0644", "root:root"),
        (".env", cmp_render.env(ctx), "0600", "root:root"),
        ("config/fluent-bit/fluent-bit.conf", _blocks.CMP_FLUENTBIT_CONF, "0644", "1000:1000"),
        ("config/fluent-bit/parsers.conf", _blocks.CMP_FLUENTBIT_PARSERS, "0644", "1000:1000"),
        ("config/fluent-bit/parse_json.lua", bundled.text("fluent-bit/parse_json.lua"), "0644", "1000:1000"),
    ]


def run(ctx) -> None:
    r = ctx.remote("cmp")
    # the backend runs as uid 1000 and writes node display names into ./config and
    # uploads into /var/orcastra/uploads (both unwritable is a known silent failure)
    r.run(f"install -d -m 0755 {DIR} && install -d -o 1000 -g 1000 -m 0755 {DIR}/config "
          f"{DIR}/config/fluent-bit && install -d -o 1000 -g 1000 -m 0750 /var/orcastra/uploads",
          what="cmp dirs")
    rendered = files(ctx)
    for rel, body, mode, owner in rendered:
        path = f"{DIR}/{rel}"
        if not r.same_content(path, body):
            r.write(path, body, mode=mode, owner=owner)
    digest = compose.config_hash(body for _, body, _, _ in rendered)
    changed = compose.needs_recreate(ctx, "cmp", digest)
    services = r.run(f"cd {DIR} && docker compose -p {T.COMPOSE_PROJECT} -f {FILE} config --services",
                     what="validate compose").out.split()
    if sorted(services) != sorted(SERVICES + ["autoheal"]):
        raise InstallError(f"Unexpected services in the release compose: {services}")
    compose.pull(r, DIR, T.COMPOSE_PROJECT, compose_file=FILE, what="pull CMP images")
    compose.up(r, DIR, T.COMPOSE_PROJECT, compose_file=FILE, recreate=changed)
    compose.wait_healthy(r, DIR, T.COMPOSE_PROJECT, SERVICES + ["autoheal"], timeout=900,
                         compose_file=FILE, running_only=["autoheal"])
    compose.mark_running(ctx, "cmp", digest)
    ctx.log.ok(f"Orcastra CMP {ctx.values['CMP_VERSION']} is healthy"
               + (" (containers recreated for new config)" if changed else ""))
