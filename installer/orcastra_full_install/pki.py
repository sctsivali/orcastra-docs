"""A private CA for OpenSearch, created with openssl on the host, replacing the bundled
demo certificates (whose private keys, including the `kirk` admin cert, are public).

Distinguished names are written in RFC 2253 order (most specific first) where OpenSearch
compares them, which is the reverse of the order openssl's -subj takes."""
from __future__ import annotations

import os
import subprocess
from typing import Dict, List

from orcastra_core.errors import InstallError

CA_DAYS, LEAF_DAYS = 3650, 1825
NODE_SUBJ = "/OU=Logging/O=Orcastra/CN=opensearch-node1"
ADMIN_SUBJ = "/OU=Logging/O=Orcastra/CN=orcastra-os-admin"
NODE_DN = "CN=opensearch-node1,O=Orcastra,OU=Logging"
ADMIN_DN = "CN=orcastra-os-admin,O=Orcastra,OU=Logging"


def _run(argv: List[str], what: str) -> None:
    cp = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, universal_newlines=True)
    if cp.returncode != 0:
        raise InstallError(f"openssl failed ({what}): {cp.stderr.strip()[-300:]}")


def ensure(pki_dir: str, node_ip: str, node_names: List[str]) -> Dict[str, str]:
    """Create ca, node and admin key pairs once. Returns their paths."""
    os.makedirs(pki_dir, mode=0o700, exist_ok=True)
    p = {"ca": "ca.crt", "ca_key": "ca.key", "node": "node.crt", "node_key": "node.key",
         "admin": "admin.crt", "admin_key": "admin.key"}
    p = {k: os.path.join(pki_dir, v) for k, v in p.items()}
    old = os.umask(0o077)
    try:
        if not os.path.exists(p["ca"]):
            _run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072",
                  "-out", p["ca_key"]], "CA key")
            _run(["openssl", "req", "-x509", "-new", "-key", p["ca_key"], "-sha256",
                  "-days", str(CA_DAYS), "-subj", "/OU=Logging/O=Orcastra/CN=Orcastra Logging CA",
                  "-addext", "basicConstraints=critical,CA:TRUE",
                  "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-out", p["ca"]], "CA cert")
        san = ",".join([f"IP:{node_ip}"] + [f"DNS:{n}" for n in node_names])
        _leaf(p, "node", NODE_SUBJ, san)
        _leaf(p, "admin", ADMIN_SUBJ, None)
    finally:
        os.umask(old)
    for k in ("ca", "node", "admin"):
        os.chmod(p[k], 0o644)
    return p


def _leaf(p: Dict[str, str], name: str, subj: str, san) -> None:
    crt, key = p[name], p[f"{name}_key"]
    if os.path.exists(crt):
        return
    csr = crt + ".csr"
    ext = crt + ".ext"
    _run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
          "-out", key], f"{name} key")
    _run(["openssl", "req", "-new", "-key", key, "-subj", subj, "-out", csr], f"{name} csr")
    lines = ["basicConstraints=CA:FALSE", "keyUsage=critical,digitalSignature,keyEncipherment",
             "extendedKeyUsage=serverAuth,clientAuth"]
    if san:
        lines.append(f"subjectAltName={san}")
    with open(ext, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    _run(["openssl", "x509", "-req", "-in", csr, "-CA", p["ca"], "-CAkey", p["ca_key"],
          "-CAcreateserial", "-days", str(LEAF_DAYS), "-sha256", "-extfile", ext, "-out", crt],
         f"{name} cert")
    os.unlink(csr)
    os.unlink(ext)


def expiry(cert: str) -> str:
    cp = subprocess.run(["openssl", "x509", "-noout", "-enddate", "-in", cert],
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        universal_newlines=True)
    return cp.stdout.strip().split("=", 1)[-1] if cp.returncode == 0 else "unknown"


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()
