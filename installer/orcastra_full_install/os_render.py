"""OpenSearch files, rendered from the blocks extracted out of
docs/deployment/vm3-opensearch.md with a small set of named edits. Each edit asserts it
matched exactly once, so a change in the guide fails loudly here instead of producing a
half-patched file."""
from __future__ import annotations

from typing import Dict

from . import _blocks
from . import pki
from . import topology as T

CERT_DIR = "/usr/share/opensearch/config"
OSD_CONF = "/usr/share/opensearch-dashboards/config"


def _sub(text: str, old: str, new: str) -> str:
    n = text.count(old)
    if n != 1:
        raise ValueError(f"expected exactly one {old!r} in the guide block, found {n}")
    return text.replace(old, new)


def compose(heap_gib: int) -> str:
    c = _blocks.OS_COMPOSE
    c = _sub(c, '"OPENSEARCH_JAVA_OPTS=-Xms4g -Xmx4g"', f'"OPENSEARCH_JAVA_OPTS=-Xms{heap_gib}g -Xmx{heap_gib}g"')
    c = _sub(c, "      - plugins.security.disabled=false\n",
             "      - plugins.security.disabled=false\n      - DISABLE_INSTALL_DEMO_CONFIG=true\n")
    c = _sub(c, "      - ./config/opensearch.yml:/usr/share/opensearch/config/opensearch.yml:ro\n",
             "      - ./config/opensearch.yml:/usr/share/opensearch/config/opensearch.yml:ro\n"
             f"      - ./config/certs/node.pem:{CERT_DIR}/node.pem:ro\n"
             f"      - ./config/certs/node-key.pem:{CERT_DIR}/node-key.pem:ro\n"
             f"      - ./config/certs/root-ca.pem:{CERT_DIR}/root-ca.pem:ro\n")
    # transport stays inside the instance (single node), only HTTPS is published
    c = _sub(c, '      - "9200:9200"\n      - "9300:9300"\n', '      - "9200:9200"\n')
    c = _sub(c, "      - ./config/opensearch_dashboards.yml:/usr/share/opensearch-dashboards/config/opensearch_dashboards.yml:ro\n",
             "      - ./config/opensearch_dashboards.yml:/usr/share/opensearch-dashboards/config/opensearch_dashboards.yml:ro\n"
             f"      - ./config/certs/root-ca.pem:{OSD_CONF}/root-ca.pem:ro\n")
    return "# Managed by orcastra-full, rendered from docs/deployment/vm3-opensearch.md\n" + c


def opensearch_yml() -> str:
    y = _blocks.OS_YML
    for demo, ours in (("esnode-key.pem", "node-key.pem"), ("esnode.pem", "node.pem")):
        y = y.replace(demo, ours)
    y = _sub(y, "plugins.security.allow_unsafe_democertificates: true\n", "")
    y = _sub(y, "  - CN=kirk,OU=client,O=client,L=test,C=de\n",
             f"  - {pki.ADMIN_DN}\nplugins.security.nodes_dn:\n  - {pki.NODE_DN}\n")
    return "# Managed by orcastra-full (private CA instead of the demo certificates)\n" + y


def internal_users(hashes: Dict[str, str]) -> str:
    u = _blocks.OS_INTERNAL_USERS
    u = _sub(u, "<BCRYPT_HASH_OF_OPENSEARCH_ADMIN_PASSWORD>", hashes["admin"])
    u = _sub(u, "<BCRYPT_HASH_OF_AUDIT_VIEWER_PASSWORD>", hashes["audit_viewer"])
    u = _sub(u, "<BCRYPT_HASH_OF_DASHBOARDS_PASSWORD>", hashes["kibanaserver"])
    if "<BCRYPT_HASH" in u:
        raise ValueError("a bcrypt placeholder survived rendering")
    return u


def dashboards_yml(password: str) -> str:
    """Literal credentials (the file is 0600) and CA verification instead of `none`."""
    y = _blocks.OSD_YML
    y = _sub(y, 'opensearch.ssl.verificationMode: none\n',
             'opensearch.ssl.verificationMode: full\n'
             f'opensearch.ssl.certificateAuthorities: ["{OSD_CONF}/root-ca.pem"]\n')
    y = _sub(y, 'opensearch.username: "${OPENSEARCH_DASHBOARDS_USER:-kibanaserver}"',
             'opensearch.username: "kibanaserver"')
    y = _sub(y, 'opensearch.password: "${OPENSEARCH_DASHBOARDS_PASSWORD:?OPENSEARCH_DASHBOARDS_PASSWORD is required}"',
             f'opensearch.password: "{password}"')
    return y


def env(secrets) -> str:
    return ("# Managed by orcastra-full\n"
            f"OPENSEARCH_ADMIN_PASSWORD={secrets.get('os_admin_password')}\n"
            f"OPENSEARCH_DASHBOARDS_PASSWORD={secrets.get('os_dashboards_password')}\n"
            "ARCHIVE_DIR=/opt/opensearch/archive\n"
            f"FLUENTBIT_PASSWORD={secrets.get('fluentbit_password')}\n")


IMAGE = f"opensearchproject/opensearch:{T.PINS['opensearch']}"
