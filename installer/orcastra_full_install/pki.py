"""The OpenSearch certificates of docs/deployment/vm3-opensearch.md Step 5, created with
openssl on the host: a private CA, the node certificate and the security admin
certificate (the demo certificates' private keys, including `kirk`, are public).

Subjects, key sizes, lifetimes and file names follow the guide, so `opensearch.yml` (taken
from the guide verbatim) matches its nodes_dn and admin_dn."""
from __future__ import annotations

import os
import subprocess
from typing import Dict, List

from orcastra_core.errors import InstallError

CA_DAYS, LEAF_DAYS = 3650, 825
CA_SUBJ = "/O=Orcastra/OU=logging/CN=Orcastra Logging Root CA"
NODE_SUBJ = "/O=Orcastra/OU=logging/CN=opensearch-node1"
ADMIN_SUBJ = "/O=Orcastra/OU=logging/CN=orcastra-logging-admin"
FILES = {"ca": "root-ca.pem", "ca_key": "root-ca-key.pem", "node": "node.pem",
         "node_key": "node-key.pem", "admin": "admin.pem", "admin_key": "admin-key.pem"}


def _run(argv: List[str], what: str) -> None:
    cp = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, universal_newlines=True)
    if cp.returncode != 0:
        raise InstallError(f"openssl failed ({what}): {cp.stderr.strip()[-300:]}")


def paths(pki_dir: str) -> Dict[str, str]:
    return {k: os.path.join(pki_dir, v) for k, v in FILES.items()}


def ensure(pki_dir: str, node_ip: str, extra_ips: List[str]) -> Dict[str, str]:
    """Create the three key pairs once. SANs: the names Dashboards and the forwarders use
    (`opensearch`, `localhost`, 127.0.0.1, the node's private IP) plus the addresses
    browsers reach Dashboards on (the guide's LOGS_DOMAIN)."""
    os.makedirs(pki_dir, mode=0o700, exist_ok=True)
    p = paths(pki_dir)
    old = os.umask(0o077)
    try:
        if not os.path.exists(p["ca"]):
            _run(["openssl", "genpkey", "-quiet", "-algorithm", "RSA", "-pkeyopt",
                  "rsa_keygen_bits:4096", "-out", p["ca_key"]], "CA key")
            _run(["openssl", "req", "-x509", "-new", "-key", p["ca_key"], "-sha256",
                  "-days", str(CA_DAYS), "-out", p["ca"], "-subj", CA_SUBJ,
                  "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                  "-addext", "keyUsage=critical,keyCertSign,cRLSign"], "CA cert")
        ips = ["127.0.0.1", node_ip] + [i for i in extra_ips if i not in ("127.0.0.1", node_ip)]
        san = ",".join(["DNS:opensearch", "DNS:localhost"] + [f"IP:{i}" for i in ips])
        _leaf(p, "node", NODE_SUBJ, ["keyUsage=critical,digitalSignature,keyEncipherment",
                                     "extendedKeyUsage=serverAuth,clientAuth",
                                     f"subjectAltName={san}"])
        _leaf(p, "admin", ADMIN_SUBJ, ["keyUsage=critical,digitalSignature",
                                       "extendedKeyUsage=clientAuth"])
    finally:
        os.umask(old)
    for k in ("ca", "node", "admin"):
        os.chmod(p[k], 0o644)
    return p


def _leaf(p: Dict[str, str], name: str, subj: str, ext: List[str]) -> None:
    crt, key = p[name], p[f"{name}_key"]
    if os.path.exists(crt):
        return
    csr, extfile = crt + ".csr", crt + ".ext"
    _run(["openssl", "genpkey", "-quiet", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072",
          "-out", key], f"{name} key")
    _run(["openssl", "req", "-new", "-key", key, "-subj", subj, "-out", csr], f"{name} csr")
    with open(extfile, "w", encoding="utf-8") as fh:
        fh.write("\n".join(["basicConstraints=CA:FALSE"] + ext) + "\n")
    _run(["openssl", "x509", "-req", "-in", csr, "-CA", p["ca"], "-CAkey", p["ca_key"],
          "-CAcreateserial", "-sha256", "-days", str(LEAF_DAYS), "-extfile", extfile, "-out", crt],
         f"{name} cert")
    os.unlink(csr)
    os.unlink(extfile)


def expiry(cert: str) -> str:
    cp = subprocess.run(["openssl", "x509", "-noout", "-enddate", "-in", cert],
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        universal_newlines=True)
    return cp.stdout.strip().split("=", 1)[-1] if cp.returncode == 0 else "unknown"


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()
