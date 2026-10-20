"""Phase - OrcaHub federation (docs/operations/orcahub-federation-sso.md, Steps 1-3). Configured
when ORCAHUB_CLIENT_ID is set, switched off (source disabled, button removed) when it was set
before and is cleared now, and proven from the outside whenever it is on. Admin access comes
from the same short-lived akadmin token as the Authentik phase, deleted at the end."""
from __future__ import annotations

from orcastra_core.errors import InstallError
from orcastra_core.retry import wait_until

from .. import authentik_api as A
from .. import orcahub as O
from ..httpapi import HttpError
from ..secrets import alnum

TITLE = "OrcaHub federation (sign in with OrcaHub)"


def _session(ctx) -> str:
    key = alnum(60)
    ctx.log.add_secret(key)
    res = A.admin_session(ctx, key, set_password=False)
    if not res.get("ok"):
        raise InstallError(f"Could not open an Authentik admin session: {res}")
    return key


def run(ctx) -> None:
    wanted = bool(ctx.values.get("ORCAHUB_CLIENT_ID"))
    had = bool(ctx.state.artifact("orcahub"))
    if not wanted and not had:
        ctx.log.ok("OrcaHub federation is not configured (later: `orcastra-full orcahub`)")
        return
    if not had:
        # recorded before the first write, so a switch-off also undoes a run that failed half way
        ctx.state.set_artifact("orcahub", {"pending": True})
    key = _session(ctx)
    try:
        if not wanted:
            O.disable(ctx, key)
            if not wait_until(lambda: O.SOURCE_NAME not in O.offered(ctx), timeout=120, interval=10):
                raise InstallError("OrcaHub was switched off but the login page still offers it.")
            ctx.state.drop_artifact("orcahub")
            ctx.log.ok("OrcaHub federation switched off: source disabled, button gone from "
                       f"{ctx.public_url('authentik')}. Users who signed in through it keep their accounts.")
            return
        out = O.configure(ctx, key)
    finally:
        try:
            A.drop_admin_session(ctx)
        except (InstallError, HttpError) as exc:
            ctx.log.warn(f"Could not delete the temporary admin token ({exc}); it expires in 45 minutes.")
    ctx.state.set_artifact("orcahub", {"client_id": ctx.values["ORCAHUB_CLIENT_ID"],
                                       "discovery": ctx.values["ORCAHUB_DISCOVERY_URL"]})
    ctx.log.ok(f"Policy '{O.POLICY_NAME}' bound to {', '.join(O.SOURCE_FLOWS)} (engine mode ALL): "
               "verified email required, upstream groups refused")
    ctx.log.ok(f"OAuth source '{O.SOURCE_NAME}' (slug {O.SLUG}) for issuer {out['issuer']}: links "
               f"by verified email only, PKCE {out['pkce']}")
    ctx.log.ok(f"Login button on {out['stage']} (labels shown)")
    for name in out["review"]:
        ctx.log.warn(f"Review the extra binding {name!r} on a source flow: with engine mode ALL it "
                     "now has to pass too.")
    last = [[]]

    def proven() -> bool:
        last[0] = O.prove(ctx, out["authorize"])
        return not last[0]
    if not wait_until(proven, timeout=120, interval=10):
        raise InstallError("OrcaHub federation is configured but not usable from a browser: "
                           + "; ".join(last[0]),
                           remediation="Check the public SSO hostname and the client from OrcaHub, "
                                       "then run `orcastra-full orcahub` again.")
    ctx.log.ok(f"{ctx.public_url('authentik')} offers OrcaHub, and its button reaches OrcaHub with "
               "this client and the registered callback")
    ctx.log.info(f"Callback registered with OrcaHub: {O.callback(ctx)}")
