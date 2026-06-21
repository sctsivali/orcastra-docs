"""Phase 12 - verify the deployment end to end (non-destructive)."""
from ..dockerutil import compose_ps, service_state, http_probe, wait_for
from ..errors import VerifyError

TITLE = "Verify deployment"

_SERVICES = ["postgres", "redis", "vault", "backend", "frontend", "nginx"]


def _all_healthy(ctx):
    rows = compose_ps(ctx)
    for svc in _SERVICES:
        state, health = service_state(rows, svc)
        if svc == "vault":
            if state != "running":
                return False
        elif state != "running" or health not in ("healthy", ""):
            return False
    return True


def run(ctx):
    if ctx.dry_run:
        ctx.log.detail("[dry-run] would verify health, HTTPS sign-in, and cert SAN.")
        return

    problems = []
    # The backend re-enters its start grace after the phase-11 recreate, so poll rather than
    # snapshot (its start_period is 120s in the compose healthcheck).
    ctx.log.info("Waiting for all services to settle ...")
    wait_for(ctx, lambda: _all_healthy(ctx), timeout=180, interval=5)
    rows = compose_ps(ctx)
    for svc in _SERVICES:
        state, health = service_state(rows, svc)
        if svc == "vault":
            ok = state == "running"  # vault has no healthcheck; running + unsealed is enough
        else:
            ok = state == "running" and (health in ("healthy", ""))
        mark = "✓" if ok else "✗"
        ctx.log.detail(f"  {mark} {svc}: state={state} health={health or 'n/a'}")
        if not ok:
            problems.append(f"{svc} ({state}/{health})")

    url = f"https://127.0.0.1:{ctx.https_port}/"
    code = http_probe(url, timeout=8)
    if code and code < 500:
        ctx.log.detail(f"  ✓ HTTPS edge answered {code} at {url}")
    else:
        problems.append(f"HTTPS edge unreachable (got {code})")

    if not _san_coherent(ctx):
        problems.append("server cert SAN does not match the configured host")

    if problems:
        raise VerifyError("verification found problems: " + "; ".join(problems),
                          remediation="Inspect: docker compose -p %s logs ; "
                                      "re-run with --repair." % ctx.project)

    ctx.log.ok("All services healthy; HTTPS sign-in served; cert SAN coherent.")
    ctx.log.info("Final manual check: sign in and run Administration -> Audit Log -> "
                 "Verify integrity (expect a valid chain).")


def _san_coherent(ctx):
    res = ctx.proc.run(["openssl", "x509", "-noout", "-ext", "subjectAltName",
                        "-in", ctx.server_crt])
    if not res.ok:
        return False
    text = res.out.replace(" ", "")
    host = ctx.cfg["host"]
    if ctx.cfg["host_is_ip"]:
        return f"IPAddress:{host}" in text or f"IP:{host}" in text
    return f"DNS:{host}" in text
