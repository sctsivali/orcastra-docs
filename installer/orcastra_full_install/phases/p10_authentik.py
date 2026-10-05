"""Phase 10 - Authentik on orca-authentik (docs/deployment/vm1-authentik.md).

Pinned compose, then everything the guide does by hand in the UI, through the API:
akadmin password and email, the role groups, the OAuth2 provider with a dedicated
signing key, the application with the exact slug the issuer depends on, and a
least-privilege role-sync service account for the CMP backend."""
from __future__ import annotations

from orcastra_core.errors import InstallError
from orcastra_core.retry import wait_until

from .. import authentik_api as A
from .. import bundled, compose
from .. import topology as T
from ..httpapi import Client, HttpError
from ..secrets import alnum

TITLE = "Authentik: deploy and configure SSO"

DIR = f"{T.REMOTE_DIR}/authentik"


def _env(ctx) -> str:
    s = ctx.secrets
    return ("# Managed by orcastra-full\n"
            f"AUTHENTIK_TAG={T.PINS['authentik']}\n"
            f"PG_PASS={s.get('authentik_pg_pass')}\n"
            f"AUTHENTIK_SECRET_KEY={s.get('authentik_secret_key')}\n"
            "AUTHENTIK_ERROR_REPORTING__ENABLED=false\n"
            "COMPOSE_PORT_HTTP=9000\n")


def _deploy(ctx) -> None:
    r = ctx.remote("authentik")
    r.run(f"install -d -m 0755 {DIR} && install -d -o 1000 -g 1000 -m 0755 "
          f"{DIR}/media {DIR}/certs {DIR}/custom-templates", what="authentik dirs")
    files = ((f"{DIR}/docker-compose.yml", bundled.text("authentik/compose.yml"), "0644"),
             (f"{DIR}/.env", _env(ctx), "0600"))
    for path, body, mode in files:
        if not r.same_content(path, body):
            r.write(path, body, mode=mode)
    digest = compose.config_hash(body for _, body, _ in files)
    changed = compose.needs_recreate(ctx, "authentik", digest)
    compose.pull(r, DIR, "authentik", what="pull Authentik images")
    compose.up(r, DIR, "authentik", recreate=changed)
    compose.wait_healthy(r, DIR, "authentik", ["postgresql", "server", "worker"], timeout=900)
    probe = Client(f"http://{ctx.ip('authentik')}:9000", timeout=10)
    if not wait_until(lambda: probe.request("GET", "/-/health/ready/", ok=(200, 204))[0] in (200, 204),
                      timeout=600, interval=5):
        raise InstallError("Authentik did not report ready on :9000 within 10 minutes.")
    compose.mark_running(ctx, "authentik", digest)
    ctx.log.ok(f"Authentik {T.PINS['authentik']} is up" + (" (containers recreated for new config)" if changed else ""))


def _admin_token(ctx) -> str:
    """Wait for the default blueprints (akadmin, flows, mappings), then open a session."""
    key = alnum(60)
    ctx.log.add_secret(key)
    result = {}

    def attempt() -> bool:
        nonlocal result
        result = A.admin_session(ctx, key, set_password=True)
        return bool(result.get("ok"))
    if not wait_until(attempt, timeout=600, interval=10):
        raise InstallError(f"Could not prepare the akadmin account: {result}",
                           remediation="Check `docker compose -p authentik logs worker` in orca-authentik.")
    if result.get("password_changed"):
        ctx.log.ok("akadmin password and email set")
    c = A.api(ctx, key)
    if not wait_until(lambda: c.get("/core/users/me/") is not None, timeout=120, interval=5):
        raise InstallError("The temporary admin token was not accepted by the Authentik API.")
    if not wait_until(lambda: A._one(c, "/flows/instances/",
                                     slug="default-provider-invalidation-flow") is not None,
                      timeout=600, interval=10):
        raise InstallError("Authentik's default flows did not appear (blueprints still applying?).")
    return key


def run(ctx) -> None:
    _deploy(ctx)
    key = _admin_token(ctx)
    try:
        ids = A.configure(ctx, key)
        ctx.log.ok("Groups role_admin, role_partner, role_tenant (akadmin in role_admin)")
        ctx.log.ok(f"Provider '{A.PROVIDER_NAME}' and application slug {T.ISSUER_SLUG}, "
                   f"redirect {ids['callback']}")
        svc = A.ensure_service_token(ctx, key, ids["groups"])
        A.prove_service_token(ctx, key, svc, ids["groups"])
        ctx.log.ok(f"Role-sync service account {A.SERVICE_USER} with token {A.API_TOKEN} "
                   "(proved: add/remove a user in role_tenant)")
        if "role_admin" not in A.role_groups_of(ctx, key, "akadmin"):
            raise InstallError("akadmin is not in role_admin after configuration.")
    finally:
        try:
            A.drop_admin_session(ctx)
        except (InstallError, HttpError) as exc:
            ctx.log.warn(f"Could not delete the temporary admin token ({exc}); it expires in 45 minutes.")

    doc = A.discovery(ctx)
    if doc.get("issuer") != ctx.issuer:
        raise InstallError(f"OIDC issuer mismatch: Authentik says {doc.get('issuer')}, "
                           f"the CMP will expect {ctx.issuer}.")
    n = A.jwks_count(ctx)
    if n < 1:
        raise InstallError("The provider publishes no signing keys (JWKS is empty).")
    ctx.log.ok(f"OIDC discovery issuer {doc['issuer']} with {n} signing key(s)")
