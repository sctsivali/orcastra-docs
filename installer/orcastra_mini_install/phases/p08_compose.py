"""Phase 8 - pull images and start the stack."""
from ..errors import DockerError
from .p03_login import image_refs, _AUTH_SIGNS

TITLE = "Pull images and start"


def run(ctx):
    if ctx.dry_run:
        ctx.log.detail("[dry-run] would run: docker compose pull && up -d")
        return

    ctx.log.info("Pulling images (this can take a few minutes) ...")
    pull = ctx.proc.run(ctx.compose_argv("pull"), mutating=True, timeout=1800)
    if not pull.ok:
        err = pull.combined.lower()
        if any(s in err for s in _AUTH_SIGNS):
            raise DockerError("Image pull was denied (authentication).",
                              remediation="Run `docker login` with an account that can access "
                                          f"{image_refs(ctx)[0]}, then re-run.")
        raise DockerError("docker compose pull failed.",
                          remediation=pull.combined.strip()[-500:] or "Check network and image tags.")
    ctx.log.ok("Images pulled.")

    ctx.log.info("Starting containers ...")
    up = ctx.proc.run(ctx.compose_argv("up", "-d"), mutating=True, timeout=600)
    if not up.ok:
        raise DockerError("docker compose up failed.",
                          remediation=up.combined.strip()[-500:] or "Inspect with: docker compose logs")
    ctx.log.ok("Containers started.")
