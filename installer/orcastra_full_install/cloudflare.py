"""Domain mode: browsers reach the four web endpoints on public HTTPS hostnames through a
Cloudflare Tunnel (docs/operations/domain-setup.md). The tunnel is remotely managed: the
operator adds the published application routes in the Cloudflare dashboard, the installer
runs the connector in orca-cmp and proves every route from the outside."""
from __future__ import annotations

from typing import List, Tuple

from . import topology as T
from .httpapi import Client, HttpError

_LABELS = {"cmp": "CMP frontend", "api": "CMP API (WebSockets on)", "authentik": "Authentik",
           "logs": "OpenSearch Dashboards"}


def routes(ctx) -> List[Tuple[str, str, str]]:
    """(public hostname, service URL as the connector in orca-cmp reaches it, label)."""
    out = []
    for svc in _LABELS:
        role, port = T.PUBLIC_PORTS[svc]
        target = "localhost" if role == "cmp" else ctx.ip(role)
        out.append((ctx.public_host(svc), f"http://{target}:{port}", _LABELS[svc]))
    return out


def log_routes(ctx, log) -> None:
    log.info("Cloudflare Zero Trust > Networks > Tunnels > your tunnel > Published application routes:")
    for host, service, label in routes(ctx):
        log.info(f"  {host:32} -> {service:26} {label}")


def probe(ctx, svc: str) -> Tuple[bool, str]:
    """Whether the public hostname of `svc` reaches the right service. The detail names what
    is wrong the way the operator can fix it."""
    base = ctx.public_url(svc)
    c = Client(base, timeout=20)
    try:
        if svc == "authentik":
            doc = c.get(f"/application/o/{T.ISSUER_SLUG}/.well-known/openid-configuration") or {}
            return doc.get("issuer") == ctx.issuer, f"issuer {doc.get('issuer')}"
        if svc == "api":
            body = c.get("/health") or {}
            return body.get("status") == "healthy", f"status {body.get('status')}"
        status = c.request("GET", "/healthz" if svc == "cmp" else "/api/status",
                           ok=tuple(range(200, 600)))[0]
        want = (200,) if svc == "cmp" else (200, 401)
        return status in want, f"HTTP {status}"
    except HttpError as exc:
        return False, _explain(exc.status, exc.body)


def _explain(status: int, body: str) -> str:
    """status 0 is a connection-level failure (DNS, TLS, refused)."""
    text = body.lower()
    if not status and ("name or service not known" in text or "nodename nor servname" in text
                           or "no address associated" in text):
        return "the hostname does not resolve: add it as a published application route of the tunnel"
    if not status and ("certificate" in text or "ssl" in text):
        return ("TLS failed: Cloudflare's free certificate covers one level below the zone only "
                "(app.example.com, not app.lab.example.com)")
    if status == 403 and "error code" in text:
        return (f"Cloudflare blocked the request ({body.strip()[:40]}): a WAF, Bot Fight Mode or "
                "Browser Integrity Check rule stops non-browser clients, which the CMP's own "
                "server-side calls are too. Skip those rules for this hostname")
    if status == 404:
        return "Cloudflare answered 404: the route is missing or points at the wrong hostname"
    if status in (502, 503, 504, 530) or "1033" in text:
        return (f"Cloudflare answered {status}: the connector is not connected or the route's "
                "service URL is wrong")
    return f"{'HTTP ' + str(status) if status else 'connection failed'}: {body.strip()[:120]}"
