"""OpenSearch files, rendered from the blocks extracted out of
docs/deployment/vm3-opensearch.md. The guide's compose, opensearch.yml, roles and
Dashboards config are used as they are, with two named edits that assert they matched
exactly once, so a change in the guide fails loudly here instead of half-applying:

- the Java heap follows the instance size instead of the guide's fixed 4g,
- `opensearch_security.cookie.secure` is false, because the installer serves Dashboards on
  the host address over plain HTTP (the guide expects HTTPS through a tunnel, and a secure
  cookie never reaches the browser over HTTP, so the login would loop).
"""
from __future__ import annotations

from typing import Dict

from . import _blocks
from . import topology as T


def _sub(text: str, old: str, new: str) -> str:
    n = text.count(old)
    if n != 1:
        raise ValueError(f"expected exactly one {old!r} in the guide block, found {n}")
    return text.replace(old, new)


def compose(heap_gib: int) -> str:
    c = _sub(_blocks.OS_COMPOSE, '"OPENSEARCH_JAVA_OPTS=-Xms4g -Xmx4g"',
             f'"OPENSEARCH_JAVA_OPTS=-Xms{heap_gib}g -Xmx{heap_gib}g"')
    return "# Managed by orcastra-full, rendered from docs/deployment/vm3-opensearch.md\n" + c


def opensearch_yml() -> str:
    return _blocks.OS_YML


def internal_users(hashes: Dict[str, str]) -> str:
    u = _blocks.OS_INTERNAL_USERS
    u = _sub(u, "<BCRYPT_HASH_OF_OPENSEARCH_ADMIN_PASSWORD>", hashes["admin"])
    u = _sub(u, "<BCRYPT_HASH_OF_AUDIT_VIEWER_PASSWORD>", hashes["audit_viewer"])
    u = _sub(u, "<BCRYPT_HASH_OF_DASHBOARDS_PASSWORD>", hashes["kibanaserver"])
    if "<BCRYPT_HASH" in u:
        raise ValueError("a bcrypt placeholder survived rendering")
    return u


def dashboards_yml() -> str:
    """The kibanaserver password is not here: it goes into the Dashboards keystore."""
    return _sub(_blocks.OSD_YML, "opensearch_security.cookie.secure: true\n",
                "# orcastra-full serves Dashboards over plain HTTP on the host address\n"
                "opensearch_security.cookie.secure: false\n")


def env(secrets, vm3_ip: str, logs_host: str) -> str:
    """The guide's Step 2 .env (same keys, same order) plus FLUENTBIT_PASSWORD (Step 11)."""
    values = {"OPENSEARCH_ADMIN_PASSWORD": secrets.get("os_admin_password"),
              "OPENSEARCH_DASHBOARDS_PASSWORD": secrets.get("os_dashboards_password"),
              "ARCHIVE_DIR": "/opt/opensearch/archive",
              "VM3_PRIVATE_IP": vm3_ip,
              "LOGS_DOMAIN": logs_host}
    keys = [l.split("=", 1)[0] for l in _blocks.OS_ENV.splitlines() if "=" in l]
    if keys != list(values):
        raise ValueError(f"the guide's .env keys changed: {keys}")
    lines = ["# Managed by orcastra-full"] + [f"{k}={values[k]}" for k in keys]
    lines.append(f"FLUENTBIT_PASSWORD={secrets.get('fluentbit_password')}")
    return "\n".join(lines) + "\n"


IMAGE = f"opensearchproject/opensearch:{T.PINS['opensearch']}"
DASHBOARDS_IMAGE = f"opensearchproject/opensearch-dashboards:{T.PINS['opensearch']}"
