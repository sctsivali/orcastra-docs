"""Phase 6 - nginx TLS server certificate. Generate a self-signed cert whose SAN matches
the confirmed host (IP: or DNS:), or install a bring-your-own cert. Reuse a matching cert."""
import os

from ..errors import ConfigError
from ..fsutil import ensure_dir, atomic_write

TITLE = "TLS server certificate"


def run(ctx):
    ensure_dir(ctx.nginx_certs_dir)
    host = ctx.cfg["host"]
    san = f"{ctx.cfg['san_type']}:{host}"

    if ctx.flags.server_cert and ctx.flags.server_key:
        _install_byo(ctx)
        ctx.log.ok("Installed bring-your-own TLS certificate.")
        return

    if os.path.exists(ctx.server_crt) and os.path.exists(ctx.server_key):
        if ctx.dry_run or _san_matches(ctx, san):
            ctx.log.ok(f"Existing server cert matches {san}; reusing.")
            return
        ctx.log.warn(f"Existing server cert SAN does not match {san}.")
        if not ctx.prompt.confirm("Regenerate the server certificate?", default=True):
            raise ConfigError("Server cert SAN does not match the chosen host.",
                              remediation="Regenerate, or pass a matching --server-cert/--server-key.")
        # back up the mismatched pair before regenerating
        atomic_write(ctx, ctx.server_crt, open(ctx.server_crt).read(), backup=True)

    _generate(ctx, host, san)
    ctx.log.ok(f"Generated self-signed server cert (SAN {san}, 825 days).")


def _generate(ctx, host, san):
    argv = ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825",
            "-keyout", ctx.server_key, "-out", ctx.server_crt,
            "-subj", f"/CN={host}", "-addext", f"subjectAltName={san}"]
    res = ctx.proc.run(argv, mutating=True, timeout=120)
    if not res.ok and not ctx.dry_run:
        raise ConfigError("openssl failed to generate the server certificate.",
                          remediation=(res.err or res.out).strip()[:400])
    if not ctx.dry_run:
        os.chmod(ctx.server_key, 0o600)


def _install_byo(ctx):
    if not (os.path.exists(ctx.flags.server_cert) and os.path.exists(ctx.flags.server_key)):
        raise ConfigError("--server-cert/--server-key path does not exist.")
    atomic_write(ctx, ctx.server_crt, open(ctx.flags.server_cert).read(), mode=0o644)
    atomic_write(ctx, ctx.server_key, open(ctx.flags.server_key).read(), mode=0o600)
    ctx.log.warn("BYO cert: ensure its SAN matches "
                 f"{ctx.cfg['san_type']}:{ctx.cfg['host']} or the browser will reject it.")


def _san_matches(ctx, san):
    res = ctx.proc.run(["openssl", "x509", "-noout", "-ext", "subjectAltName", "-in", ctx.server_crt])
    if not res.ok:
        return False
    text = res.out.replace(" ", "")
    # openssl prints "IP Address:1.2.3.4" or "DNS:name"
    host = ctx.cfg["host"]
    if ctx.cfg["host_is_ip"]:
        return f"IPAddress:{host}" in text or f"IP:{host}" in text
    return f"DNS:{host}" in text
