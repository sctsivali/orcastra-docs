"""Phase 11 - adaptive first-admin bootstrap.

Default: emit `bootstrap-admin.sh` for the operator to run on their own workstation, so the
admin private key is born on the laptop (never on the server).
--quick: do it server-side in /dev/shm (through nginx on localhost so X-SSL-* + the proxy
secret are injected), hand over admin.p12, shred the key material.

Both paths then close the one-shot window: blank BOOTSTRAP_ADMIN_TOKEN and recreate backend.
"""
import os
import shutil
import tempfile

from ..errors import BootstrapError
from ..fsutil import atomic_write
from .. import templates

TITLE = "Bootstrap first admin"


def run(ctx):
    if ctx.dry_run:
        ctx.log.detail("[dry-run] would enroll the first admin (client helper, or --quick "
                       "server-side) and close the one-shot window.")
        return

    if ctx.flags.skip_admin_bootstrap:
        ctx.log.warn("--skip-admin-bootstrap: see bootstrap-admin.sh / the docs to enroll later.")
        _write_client_helper(ctx)
        return

    token = templates.get_env_value(_env(ctx), "BOOTSTRAP_ADMIN_TOKEN") or ""
    if not token:
        ctx.log.ok("Bootstrap window already closed (token blank); admin already enrolled.")
        return

    _write_close_helper(ctx)

    if ctx.flags.quick:
        _server_side(ctx, token)
        _close_window(ctx)
    else:
        _write_client_helper(ctx)
        _client_instructions(ctx)
        if ctx.interactive and ctx.prompt.confirm(
                "Have you run bootstrap-admin.sh and successfully signed in as admin?",
                default=False):
            _close_window(ctx)
        else:
            ctx.log.warn("Bootstrap window left OPEN. After enrolling, run "
                         f"{ctx.bootstrap_close_path} to close it.")


# -- server-side (--quick) ---------------------------------------------------
def _server_side(ctx, token):
    if not shutil.which("curl"):
        raise BootstrapError("curl is required for --quick bootstrap.",
                             remediation="Install curl, or use the default client-helper mode.")
    tmp = tempfile.mkdtemp(dir="/dev/shm" if os.path.isdir("/dev/shm") else None,
                           prefix="orcastra-admin-")
    p12_pass = __import__("secrets").token_urlsafe(18)
    ctx.log.add_secret(p12_pass)
    try:
        key, crt, p12 = (os.path.join(tmp, n) for n in ("admin.key", "admin.crt", "admin.p12"))
        cn = ctx.flags.admin_cn
        _openssl(ctx, ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825",
                       "-keyout", key, "-out", crt, "-subj", f"/CN={cn}"])
        _openssl(ctx, ["pkcs12", "-export", "-passout", f"pass:{p12_pass}",
                       "-inkey", key, "-in", crt, "-out", p12], secret=[f"pass:{p12_pass}"])
        url = f"https://127.0.0.1:{ctx.https_port}/api/v1/auth/cert-bootstrap"
        code, body = _curl_bootstrap(ctx, url, crt, key, token)
        if code != 200:
            raise BootstrapError(f"cert-bootstrap returned HTTP {code}.",
                                 remediation=_bootstrap_hint(code, body))
        # Hand over the .p12 (the only artifact retained); shred the key material.
        shutil.move(p12, ctx.admin_p12_path)
        os.chmod(ctx.admin_p12_path, 0o600)
        ctx.state.set_artifact("admin_enrolled", True)
        ctx.state.set_artifact("admin_p12", ctx.admin_p12_path)
        ctx.log.ok(f"First admin enrolled. admin.p12 -> {ctx.admin_p12_path}")
        print()
        ctx.log.banner("Import admin.p12 into your browser - password shown once")
        print(f"  PKCS#12 password: {p12_pass}")
        print()
        ctx.log.warn("Best practice: after first login, issue a fresh admin identity from the "
                     "UI and revoke this bootstrap certificate.")
    finally:
        _shred_dir(ctx, tmp)


def _openssl(ctx, args, secret=()):
    res = ctx.proc.run(["openssl"] + args, secret_args=list(secret), mutating=True, timeout=120)
    if not res.ok:
        raise BootstrapError("openssl failed during admin cert generation.",
                             remediation=res.combined[-300:])


def _curl_bootstrap(ctx, url, crt, key, token):
    argv = ["curl", "-sk", "-o", "-", "-w", "\n%{http_code}", url,
            "--cert", crt, "--key", key, "-H", "Content-Type: application/json",
            "-d", '{"bootstrap_token":"%s"}' % token]
    res = ctx.proc.run(argv, secret_args=['{"bootstrap_token":"%s"}' % token], mutating=True, timeout=60)
    out = res.out.rstrip("\n")
    code_str = out.rsplit("\n", 1)[-1] if "\n" in out else out
    body = out[: -len(code_str)].rstrip("\n") if code_str.isdigit() else out
    try:
        return int(code_str), body
    except ValueError:
        return 0, res.combined


def _shred_dir(ctx, tmp):
    if ctx.dry_run:
        return
    for name in os.listdir(tmp):
        p = os.path.join(tmp, name)
        if shutil.which("shred"):
            ctx.proc.run(["shred", "-u", p], mutating=True)
        if os.path.exists(p):
            try:
                with open(p, "wb") as fh:
                    fh.write(os.urandom(2048))
                os.unlink(p)
            except OSError:
                pass
    shutil.rmtree(tmp, ignore_errors=True)


# -- client helper + instructions --------------------------------------------
def _write_client_helper(ctx):
    base = ctx.base_url if not ctx.cfg.get("tunnel") else f"https://localhost:{ctx.https_port}"
    token = templates.get_env_value(_env(ctx), "BOOTSTRAP_ADMIN_TOKEN") or "<bootstrap-token>"
    ctx.log.add_secret(token)
    body = _CLIENT_HELPER.format(base=base, token=token, cn=ctx.flags.admin_cn)
    atomic_write(ctx, ctx.bootstrap_helper_path, body, mode=0o700, backup=False)


def _client_instructions(ctx):
    ctx.log.info("Enroll the first admin from YOUR workstation (keeps the key off the server):")
    ctx.log.info(f"  1) Copy the helper to your machine:  scp <user>@<server>:{ctx.bootstrap_helper_path} .")
    if ctx.cfg.get("tunnel"):
        ctx.log.info(f"  2) Open the tunnel:  ssh -L {ctx.https_port}:127.0.0.1:{ctx.https_port} <user>@<server>")
        ctx.log.info("  3) Run:  ./bootstrap-admin.sh   (uses https://localhost)")
    else:
        ctx.log.info(f"  2) Run:  ./bootstrap-admin.sh   (targets {ctx.base_url})")
    ctx.log.info("  3) Import admin.p12 into your browser, then open the dashboard URL.")


# -- close the one-shot window -----------------------------------------------
def _close_window(ctx):
    text = _env(ctx)
    atomic_write(ctx, ctx.env_path, templates.set_env_value(text, "BOOTSTRAP_ADMIN_TOKEN", ""),
                 mode=0o600)
    up = ctx.proc.run(ctx.compose_argv("up", "-d", "backend"), mutating=True, timeout=300)
    if not up.ok:
        raise BootstrapError("failed to recreate backend after blanking the bootstrap token.",
                             remediation=up.combined[-300:])
    ctx.state.set_artifact("bootstrap_closed", True)
    ctx.log.ok("Bootstrap window closed (BOOTSTRAP_ADMIN_TOKEN blanked, backend recreated).")


def _write_close_helper(ctx):
    body = _CLOSE_HELPER.format(project=ctx.project)
    atomic_write(ctx, ctx.bootstrap_close_path, body, mode=0o750, backup=False)


def _env(ctx):
    return open(ctx.env_path, encoding="utf-8").read()


def _bootstrap_hint(code, body):
    if code == 403:
        return "403 means the token is wrong or bootstrap is already disabled."
    if code == 409:
        return "409 means an admin already exists - bootstrap is closed."
    if code == 401:
        return "401 means no trusted client cert reached the backend (check AUTH_PROXY_SECRET/nginx)."
    return (body or "")[-300:]


_CLIENT_HELPER = """#!/usr/bin/env bash
# Run on YOUR workstation (where the browser is). Generates a client certificate, enrolls it
# as the first admin, and produces admin.p12 to import into the browser.
set -euo pipefail
BASE="${{1:-{base}}}"
TOKEN="{token}"
echo "Generating a self-signed client certificate (the app trusts it by fingerprint) ..."
openssl req -x509 -newkey rsa:2048 -nodes -days 825 -keyout admin.key -out admin.crt -subj "/CN={cn}"
openssl pkcs12 -export -inkey admin.key -in admin.crt -out admin.p12   # set a password when prompted
echo "Enrolling as admin at $BASE ..."
curl -sk "$BASE/api/v1/auth/cert-bootstrap" \\
  --cert admin.crt --key admin.key \\
  -H 'Content-Type: application/json' \\
  -d "{{\\"bootstrap_token\\":\\"$TOKEN\\"}}"
echo
echo "Now import admin.p12 into your browser/OS keychain, then open $BASE"
echo "After signing in, close the bootstrap window on the server: ./close-bootstrap.sh"
"""

_CLOSE_HELPER = """#!/usr/bin/env bash
# Closes the one-shot admin bootstrap window: blanks BOOTSTRAP_ADMIN_TOKEN and recreates the
# backend. Run on the server, from the install directory.
set -euo pipefail
cd "$(dirname "$0")"
sed -i 's/^BOOTSTRAP_ADMIN_TOKEN=.*/BOOTSTRAP_ADMIN_TOKEN=/' .env
docker compose -p {project} -f docker-compose.yml --env-file .env up -d backend
echo "Bootstrap window closed (token blanked, backend recreated)."
"""
