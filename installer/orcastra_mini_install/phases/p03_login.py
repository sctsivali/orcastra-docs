"""Phase 3 - registry access. Verify the (private) images are pullable; on an auth failure,
hand the terminal to `docker login` so Docker drives its own device-code prompt, then retry."""
from ..errors import DockerError

TITLE = "Registry access"

_AUTH_SIGNS = ("401 unauthorized", "pull access denied", "requested access to the resource is denied",
               "authentication required", "denied: requested access", "must be logged in")


def image_refs(ctx):
    tag = ctx.cfg.get("image_tag", "1.0.0-RC1")
    backend = ctx.flags.image_backend or f"svlct/orcastra-dashboard-mini:backend-{tag}"
    frontend = ctx.flags.image_frontend or f"svlct/orcastra-dashboard-mini:frontend-{tag}"
    return backend, frontend


def _access_ok(ctx, ref):
    """Cheap access probe via manifest inspect (no image download)."""
    res = ctx.proc.run(["docker", "manifest", "inspect", ref], timeout=60)
    if res.ok:
        return True, ""
    return False, res.combined.lower()


def run(ctx):
    if ctx.dry_run:
        ctx.log.detail("[dry-run] skipping registry access probe.")
        return
    backend, frontend = image_refs(ctx)
    ctx.log.info(f"Checking access to {backend} ...")
    ok, err = _access_ok(ctx, backend)
    if ok:
        ctx.log.ok("Image access confirmed.")
        return

    if any(s in err for s in _AUTH_SIGNS):
        ctx.log.warn("These images are private - a Docker Hub login is required.")
        if not ctx.interactive:
            raise DockerError("Registry authentication required in non-interactive mode.",
                              remediation="Run `docker login` (as this user) first, then re-run "
                                          "with --non-interactive.")
        ctx.log.info("Running `docker login` (follow the prompt / device-code URL) ...")
        rc = ctx.proc.run_interactive(["docker", "login"])
        if rc != 0:
            raise DockerError("docker login failed.",
                              remediation="Re-run and complete the login, or check your account.")
        ok, err = _access_ok(ctx, backend)
        if not ok:
            raise DockerError("Logged in but still cannot access the image.",
                              remediation=f"The account lacks access to {backend}. "
                                          "Ask for repository access.")
        ctx.log.ok("Authenticated and image access confirmed.")
        return

    # Non-auth failure (network, manifest unknown, manifest cmd unsupported): don't hard-fail
    # here - defer to the real pull in phase 8, which gives a definitive error.
    ctx.log.warn("Could not confirm image access (non-auth error); will retry at pull time.")
    ctx.log.detail(err.strip()[:300])
