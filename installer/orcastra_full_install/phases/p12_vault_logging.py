"""Phase 12 - Fluent Bit on orca-vault (docs/deployment/vm2-vault.md, step 7): ships the
Vault audit log to OpenSearch. Installed from Fluent Bit's signed apt repository at a
pinned version instead of piping the upstream install script from `master`, and it
verifies the OpenSearch certificate against the installer's CA."""
from __future__ import annotations

from orcastra_core.errors import InstallError
from orcastra_core.retry import wait_until

from .. import _blocks, pki
from .. import topology as T

TITLE = "Fluent Bit on orca-vault (Vault audit log -> OpenSearch)"

_INSTALL = r"""
if [ "$(dpkg-query -W -f '${{Version}}' fluent-bit 2>/dev/null)" != "{ver}" ]; then
  for i in 1 2 3 4 5; do
    curl -fsSL https://packages.fluentbit.io/fluentbit.key | gpg --dearmor --yes -o /usr/share/keyrings/fluentbit-keyring.gpg && break
    sleep $((i * 3))
  done
  . /etc/os-release
  echo "deb [signed-by=/usr/share/keyrings/fluentbit-keyring.gpg] https://packages.fluentbit.io/ubuntu/${{UBUNTU_CODENAME:-$VERSION_CODENAME}} ${{UBUNTU_CODENAME:-$VERSION_CODENAME}} main" > /etc/apt/sources.list.d/fluent-bit.list
  for i in 1 2 3 4 5; do
    apt-get update -q && apt-get install -y -q --allow-downgrades "fluent-bit={ver}" && break
    sleep $((i * 5))
  done
  apt-mark hold fluent-bit >/dev/null
fi
dpkg -s fluent-bit >/dev/null
install -d -m 0750 /var/lib/fluent-bit/storage
"""

CA_PATH = "/etc/fluent-bit/orcastra-ca.pem"


def conf(ctx) -> str:
    c = _blocks.VAULT_FLUENTBIT_CONF
    for old, new in (("<VM3_PRIVATE_IP>", ctx.ip("opensearch")),
                     ("<FLUENTBIT_PASSWORD_FROM_VM3>", ctx.secrets.get("fluentbit_password")),
                     ("    tls.verify        Off\n",
                      f"    tls.verify        On\n    tls.ca_file       {CA_PATH}\n")):
        if c.count(old) != 1:
            raise InstallError(f"internal error: guide block changed, cannot place {old.strip()!r}")
        c = c.replace(old, new)
    return "# Managed by orcastra-full, rendered from docs/deployment/vm2-vault.md\n" + c


def parsers(current: str) -> str:
    """The guide says to make sure parsers.conf CONTAINS vault_json (append, never replace
    the package's own parsers)."""
    if "Name        vault_json" in current or "Name vault_json" in current:
        return current
    return current.rstrip("\n") + "\n\n" + _blocks.VAULT_FLUENTBIT_PARSER


def run(ctx) -> None:
    r = ctx.remote("vault")
    r.run(_INSTALL.format(ver=T.PINS["fluentbit_apt"]), what="install Fluent Bit", timeout=1200)
    changed = False
    ca = pki.read(f"{ctx.pki_dir}/ca.crt")
    for path, body, mode in ((CA_PATH, ca, "0644"), ("/etc/fluent-bit/fluent-bit.conf", conf(ctx), "0600")):
        if not r.same_content(path, body):
            r.write(path, body, mode=mode)
            changed = True
    current = r.read("/etc/fluent-bit/parsers.conf") or ""
    merged = parsers(current)
    if merged != current:
        r.write("/etc/fluent-bit/parsers.conf", merged)
        changed = True
    r.run("systemctl enable fluent-bit >/dev/null 2>&1; "
          + ("systemctl restart fluent-bit" if changed else "systemctl start fluent-bit"),
          what="start Fluent Bit")
    if not wait_until(lambda: r.ok("systemctl is-active --quiet fluent-bit"), timeout=60):
        log = r.run("journalctl -u fluent-bit -n 30 --no-pager", check=False).out
        raise InstallError("Fluent Bit did not stay running on orca-vault:\n" + log[-800:])
    ctx.log.ok(f"Fluent Bit {T.PINS['fluentbit_apt']} ships /var/log/vault/audit.log to "
               f"https://{ctx.ip('opensearch')}:9200 (certificate verified)")
