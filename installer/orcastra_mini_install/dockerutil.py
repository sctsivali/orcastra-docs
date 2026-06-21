"""Docker/compose helpers shared by phases: ps parsing, health polling, log tailing,
and an unverified-TLS HTTP probe for the self-signed edge."""
import json
import ssl
import time
import urllib.request


def compose_ps(ctx):
    """Return a list of per-service dicts from `docker compose ps --format json`. Handles
    both the JSON-lines and JSON-array shapes different compose versions emit."""
    res = ctx.proc.run(ctx.compose_argv("ps", "--format", "json"))
    if not res.ok or not res.out.strip():
        return []
    out = res.out.strip()
    rows = []
    try:
        parsed = json.loads(out)
        rows = parsed if isinstance(parsed, list) else [parsed]
    except ValueError:
        for line in out.splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


def service_state(rows, service):
    """Return (state, health) for a service name, or (None, None)."""
    for r in rows:
        if r.get("Service") == service or r.get("Name", "").endswith("-" + service):
            return r.get("State"), (r.get("Health") or "")
    return None, None


def tail_logs(ctx, service, lines=50):
    res = ctx.proc.run(ctx.compose_argv("logs", "--no-color", "--tail", str(lines), service))
    return res.combined.strip()


def http_probe(url, timeout=5):
    """GET a (possibly self-signed) HTTPS URL. Returns the status code or None on failure."""
    ctxssl = ssl.create_default_context()
    ctxssl.check_hostname = False
    ctxssl.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=ctxssl) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code  # 4xx/3xx still means the edge answered
    except Exception:
        return None


def wait_for(ctx, predicate, *, timeout, interval=5, what="condition"):
    """Poll `predicate()` until truthy or timeout. Returns the last truthy value or None.
    (Runs inside the installer process; ordinary time.sleep is fine here.)"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    return last if last else None
