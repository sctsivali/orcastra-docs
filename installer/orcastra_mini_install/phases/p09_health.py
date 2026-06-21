"""Phase 9 - wait for the data tier. Gate only on postgres + redis healthy and vault
running. The backend stays unhealthy until phase 10 supplies VAULT_TOKEN, so we do NOT
wait on it here."""
from ..dockerutil import compose_ps, service_state, tail_logs, wait_for
from ..errors import InstallError

TITLE = "Wait for data tier"


def run(ctx):
    if ctx.dry_run:
        ctx.log.detail("[dry-run] would wait for postgres/redis healthy + vault running.")
        return

    def ready():
        rows = compose_ps(ctx)
        pg = service_state(rows, "postgres")
        rd = service_state(rows, "redis")
        va = service_state(rows, "vault")
        ctx.log.debug(f"health: postgres={pg} redis={rd} vault={va}")
        ok = (pg[1] == "healthy" and rd[1] == "healthy" and va[0] == "running")
        return ok

    ctx.log.info("Waiting for postgres, redis, and vault ...")
    if not wait_for(ctx, ready, timeout=180, interval=5):
        _diagnose(ctx)
        raise InstallError("data tier did not become ready within 180s.",
                           remediation="See the container logs above; check disk space and "
                                       "POSTGRES_* values in .env.")
    ctx.log.ok("postgres + redis healthy, vault running (sealed - phase 10 unseals it).")


def _diagnose(ctx):
    rows = compose_ps(ctx)
    for svc in ("postgres", "redis", "vault"):
        state, health = service_state(rows, svc)
        if state != "running" or (health and health != "healthy"):
            ctx.log.warn(f"{svc}: state={state} health={health}")
            ctx.log.detail(tail_logs(ctx, svc, 30))
