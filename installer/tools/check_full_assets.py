#!/usr/bin/env python3
"""Fail when the Full installer's guide-derived blocks drift from the deployment guides.

Re-extracts the blocks from docs/deployment/vm2..vm4 and compares them with the committed
orcastra_full_install/_blocks.py, then checks that every installer-side edit of those
blocks still applies (the renderers assert each edit matches exactly once).

Files the installer owns outright, and why:
- assets/authentik/compose.yml: the guide downloads the latest upstream compose, the
  installer pins Authentik and drops the Docker socket and the HTTPS port.
- Vault's vault.hcl (phases/p09_vault.py): the guide's file has no storage stanza.
- assets/cmp/<version>/docker-compose.prod.yml: the release compose, checked by sha256.
- assets/opensearch/dashboards/*.ndjson and assets/fluent-bit/parse_json.lua: release
  files the guide tells the reader to fetch from the (private) application repository.

  python3 installer/tools/check_full_assets.py
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALLER = os.path.dirname(HERE)
sys.path.insert(0, INSTALLER)
sys.path.insert(0, HERE)

import gen_full_blocks  # noqa: E402
from orcastra_full_install import _blocks, cmp_render, os_render, topology  # noqa: E402


class _Secrets:
    def get(self, key):
        return "x" * 32

    def maybe(self, key):
        return "x" * 32


class _Ctx:
    """Just enough context for the renderers."""
    values = {"CMP_VERSION": topology.CMP_LATEST, "SIZING": "compact",
              "HOST_ADDRESS": "192.0.2.10", "PORT_AUTHENTIK": "9000", "PORT_CMP": "4321",
              "PORT_API": "8765", "PORT_LOGS": "5601"}
    secrets = _Secrets()
    sizes = topology.SIZING["compact"]
    issuer = "http://192.0.2.10:9000/application/o/orcastra-dashboard/"

    def ip(self, role):
        return {"vault": "10.77.0.11", "authentik": "10.77.0.12", "opensearch": "10.77.0.13",
                "cmp": "10.77.0.14"}[role]

    def public_url(self, svc):
        return "http://192.0.2.10:" + {"cmp": "4321", "api": "8765", "authentik": "9000", "logs": "5601"}[svc]


def main() -> int:
    errors = []
    fresh = gen_full_blocks.extract()
    for name, value in fresh.items():
        if getattr(_blocks, name, None) != value:
            errors.append(f"{name} differs from the guide; run tools/gen_full_blocks.py")
    try:
        os_render.compose(3)
        os_render.opensearch_yml()
        os_render.internal_users({"admin": "a", "audit_viewer": "b", "kibanaserver": "c"})
        os_render.dashboards_yml("p")
        cmp_render.env(_Ctx())
    except Exception as exc:  # noqa: BLE001 - every failure is reported
        errors.append(f"renderer no longer matches the guide: {exc}")
    for ver, digest in topology.CMP_COMPOSE_SHA256.items():
        path = os.path.join(INSTALLER, "orcastra_full_install", "assets", "cmp", ver,
                            "docker-compose.prod.yml")
        with open(path, "rb") as fh:
            if hashlib.sha256(fh.read()).hexdigest() != digest:
                errors.append(f"assets/cmp/{ver}/docker-compose.prod.yml does not match its sha256")
    if errors:
        print("Full installer asset parity FAILED:\n  - " + "\n  - ".join(errors))
        return 1
    print(f"Full installer asset parity OK ({len(fresh)} guide blocks, renderers apply, "
          f"{len(topology.CMP_COMPOSE_SHA256)} release compose checksum(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
