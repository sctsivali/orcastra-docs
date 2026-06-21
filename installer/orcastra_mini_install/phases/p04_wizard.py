"""Phase 4 - configuration. Auto-detect the host address and reconfirm it, then resolve
ports/TTLs. The confirmed host fills the cert SAN and all three URLs coherently."""
from ..errors import AbortByUser, ConfigError
from .. import netutil

TITLE = "Configuration"


def run(ctx):
    f = ctx.flags
    host, host_is_ip = _resolve_host(ctx)

    port = int(f.https_port)
    base_url = f"https://{host}:{port}"
    san_type = "IP" if host_is_ip else "DNS"

    # Coherence guard: the cert SAN type must match the URL host (the #1 cause of a browser
    # rejecting the server cert). Re-assert here before anything is written.
    if host_is_ip and not netutil.is_ip_literal(host):
        raise ConfigError(f"host '{host}' classified as IP but is not a valid IP literal.")
    if not host_is_ip and netutil.is_ip_literal(host):
        raise ConfigError(f"host '{host}' is an IP but was classified as DNS.")

    ttls = _resolve_ttls(ctx)

    ctx.cfg.update({
        "host": host,
        "host_is_ip": host_is_ip,
        "san_type": san_type,
        "https_port": port,
        "base_url": base_url,
        "container_prefix": f.container_prefix,
        "image_tag": f.version_tag,
        "postgres_user": f.postgres_user,
        "postgres_db": f.postgres_db,
        "tunnel": bool(f.tunnel),
        **ttls,
    })
    ctx.state.set_config({k: ctx.cfg[k] for k in (
        "host", "host_is_ip", "san_type", "https_port", "base_url", "container_prefix",
        "image_tag", "postgres_user", "postgres_db", "tunnel",
        "client_cert_ttl_days", "jwt_ttl_seconds", "ca_ttl_hours", "role_max_ttl_hours")})

    ctx.log.ok(f"Dashboard URL: {base_url}  (cert SAN: {san_type}:{host})")
    if ctx.cfg["tunnel"]:
        ctx.log.info(f"SSH tunnel mode - on your workstation run: "
                     f"ssh -L {port}:127.0.0.1:{port} <user>@<this-server>")


def _resolve_host(ctx):
    f = ctx.flags
    if f.tunnel:
        ctx.log.info("Tunnel mode: the dashboard is reached as https://localhost.")
        return "localhost", False
    if f.host:
        is_ip = f.host_is_ip if f.host_is_ip is not None else netutil.is_ip_literal(f.host)
        return f.host, is_ip

    candidates = netutil.candidate_ips(ctx.proc)
    fq = netutil.fqdn()
    primary = candidates[0] if candidates else None

    if not ctx.interactive:
        if not primary:
            raise AbortByUser("Could not auto-detect a host IP in non-interactive mode.",
                              remediation="Pass --host <ip-or-name> (and --host-is-ip/--host-is-dns).")
        ctx.log.info(f"Using detected IP {primary} (non-interactive).")
        return primary, True

    if primary:
        ctx.log.info(f"Detected primary address: {primary}  (source IP of the default route)")
    extras = [c for c in candidates[1:]]
    if fq:
        extras.append(f"{fq} (hostname)")
    if extras:
        ctx.log.detail("Other candidates: " + ", ".join(extras))

    default = primary or (fq or "")
    answer = ctx.prompt.ask(
        f"Address operators will use to reach the dashboard (https://<this>:{f.https_port})",
        default=default, key="host")
    answer = answer.strip()
    if not answer:
        raise AbortByUser("No host provided.")
    is_ip = netutil.is_ip_literal(answer)
    if is_ip:
        ctx.log.detail(f"'{answer}' -> IP literal (cert SAN will be IP:{answer}).")
    else:
        ctx.log.detail(f"'{answer}' -> hostname (cert SAN will be DNS:{answer}).")
    return answer, is_ip


def _resolve_ttls(ctx):
    f = ctx.flags
    client_days = int(f.client_cert_ttl_days)
    jwt = int(f.jwt_ttl_seconds)
    ca = int(f.ca_ttl_hours)
    role = int(f.role_max_ttl_hours)
    leaf_hours = client_days * 24

    # Vault refuses to issue a leaf whose notAfter exceeds the CA. Keep CA > role >= leaf.
    if role < leaf_hours:
        ctx.log.warn(f"role max_ttl ({role}h) < client cert TTL ({leaf_hours}h); raising role to {leaf_hours}h.")
        role = leaf_hours
    if ca <= role:
        new_ca = role * 10
        ctx.log.warn(f"CA TTL ({ca}h) must exceed role max_ttl ({role}h); raising CA to {new_ca}h.")
        ca = new_ca
    return {"client_cert_ttl_days": client_days, "jwt_ttl_seconds": jwt,
            "ca_ttl_hours": ca, "role_max_ttl_hours": role}
