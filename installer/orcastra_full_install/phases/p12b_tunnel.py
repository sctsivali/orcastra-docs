"""Phase - Cloudflare Tunnel connector for the public HTTPS hostnames (domain mode).

cloudflared comes from the pinned GitHub release, checked against its sha256, and runs in
orca-cmp (where docs/operations/domain-setup.md puts it) as orcastra-cloudflared.service: the
token sits in a 0600 environment file, never on a command line, self-update is off and the
metrics endpoint listens on loopback only. Outside domain mode the phase removes a connector
an earlier run installed. The Authentik and Dashboards routes are proven from this host
through Cloudflare here; the CMP routes are proven by the verify phase, once the CMP runs."""
from __future__ import annotations

from orcastra_core.errors import InstallError
from orcastra_core.retry import wait_until

from .. import cloudflare
from .. import config as C
from .. import topology as T

TITLE = "Cloudflare Tunnel connector (public HTTPS hostnames)"

SERVICE = "orcastra-cloudflared.service"
UNIT_PATH = f"/etc/systemd/system/{SERVICE}"
ENV_PATH = "/etc/orcastra/cloudflared.env"
METRICS = "127.0.0.1:20241"

def unit(ctx) -> str:
    """The connector runs sandboxed: no privileges, no writable system, no Docker socket or
    deployment files, and no route to Vault or to the LXD host whatever is published in the
    Cloudflare dashboard (the tunnel is remotely managed)."""
    return f"""[Unit]
Description=Orcastra: Cloudflare Tunnel connector (managed by orcastra-full)
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
EnvironmentFile={ENV_PATH}
ExecStart=/usr/bin/cloudflared --no-autoupdate --metrics {METRICS} tunnel run
Restart=on-failure
RestartSec=5s
TimeoutStartSec=0
DynamicUser=yes
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
SystemCallArchitectures=native
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
InaccessiblePaths=-/run/docker.sock -/var/run/docker.sock -/var/lib/docker -/opt/orcastra -/etc/orcastra
IPAddressDeny={ctx.ip('vault')}/32 {ctx.bridge_gateway}/32

[Install]
WantedBy=multi-user.target
"""


def _install_binary(r) -> str:
    ver = T.PINS["cloudflared"]
    arch = r.run("dpkg --print-architecture", what="architecture").out.strip()
    sha = T.CLOUDFLARED_DEB_SHA256.get(arch)
    if sha is None:
        raise InstallError(f"No pinned cloudflared build for architecture {arch}.")
    have = r.run("cloudflared --version 2>/dev/null || true", check=False).out
    if f"version {ver} " not in have + " ":
        url = (f"https://github.com/cloudflare/cloudflared/releases/download/{ver}/"
               f"cloudflared-linux-{arch}.deb")
        r.run(f"t=$(mktemp -d); trap 'rm -rf \"$t\"' EXIT; "
              f"curl -fsSL --retry 3 -o \"$t/cloudflared.deb\" {url}; "
              f"echo '{sha}  '\"$t/cloudflared.deb\" | sha256sum -c --quiet; "
              f"dpkg -i \"$t/cloudflared.deb\" >/dev/null",
              what=f"install cloudflared {ver}", timeout=600)
    return ver


def _remove(ctx, r) -> None:
    if not r.ok(f"test -e {UNIT_PATH}"):
        return
    r.run(f"systemctl disable --now {SERVICE} >/dev/null 2>&1 || true; rm -f {UNIT_PATH} {ENV_PATH}; "
          "systemctl daemon-reload; dpkg -r cloudflared >/dev/null 2>&1 || true", what="remove cloudflared")
    ctx.log.ok("Removed the Cloudflare Tunnel connector (domain mode is off). The tunnel and "
               "its routes stay in your Cloudflare account until you delete them there.")


def run(ctx) -> None:
    r = ctx.remote("cmp")
    if not ctx.domain_mode:
        _remove(ctx, r)
        ctx.log.ok("Domain mode is off: the web endpoints are served on the host address")
        return
    token = ctx.secrets.get("cloudflare_tunnel_token")
    tunnel = C.tunnel_id(token)
    ver = _install_binary(r)
    changed = False
    env = f"TUNNEL_TOKEN={token}\n"
    if not r.same_content(ENV_PATH, env):
        r.write(ENV_PATH, env, mode="0600", what="tunnel token")
        changed = True
    body = unit(ctx)
    if not r.same_content(UNIT_PATH, body):
        r.write(UNIT_PATH, body, what=f"unit {SERVICE}")
        r.run("systemctl daemon-reload", what="daemon-reload")
        changed = True
    r.run(f"systemctl enable {SERVICE} >/dev/null 2>&1; systemctl "
          f"{'restart' if changed else 'start'} {SERVICE}", what=f"start {SERVICE}", timeout=120)
    if not wait_until(lambda: r.ok(f"curl -fsS -o /dev/null http://{METRICS}/ready"), timeout=180, interval=5):
        raise InstallError(f"cloudflared did not connect tunnel {tunnel} to Cloudflare within 3 minutes.",
                           remediation="Run `journalctl -u orcastra-cloudflared -n 50` in orca-cmp. A "
                                       "revoked or wrong token, or blocked egress to Cloudflare "
                                       "(TCP 443 / UDP 7844), are the usual causes.")
    ctx.log.ok(f"cloudflared {ver} connected tunnel {tunnel}" + (" (restarted for new settings)" if changed else ""))
    cloudflare.log_routes(ctx, ctx.log)
    for svc in ("authentik", "logs"):
        last = [""]

        def reached(_svc=svc) -> bool:
            ok, last[0] = cloudflare.probe(ctx, _svc)
            return ok
        if not wait_until(reached, timeout=240, interval=10):
            raise InstallError(f"{ctx.public_url(svc)} does not reach {T.name(T.PUBLIC_PORTS[svc][0])}: {last[0]}",
                               remediation="Check the tunnel's published application routes against "
                                           "the table above, then re-run the installer.")
        ctx.log.ok(f"{ctx.public_url(svc)} reaches {T.name(T.PUBLIC_PORTS[svc][0])} through the tunnel")
