"""OrcaHub federation through the Authentik API, the way docs/operations/orcahub-federation-sso.md
Steps 1-3 click it: the upstream OpenID Connect source, the verified-email gate bound to both
source flows with policy engine mode ALL, and the button on the login page.

Identity is federated, authorization is not: the source links an existing user by email only
behind the verified-email policy, and the same policy refuses any sign-in that carries upstream
groups (the CMP grants roles by group name), so an upstream claim can never grant a local role.
The policy expression comes verbatim from the guide (_blocks).
"""
from __future__ import annotations

import json
import shlex
import urllib.parse
from typing import Any, Dict, List, Optional

from orcastra_core.errors import InstallError

from . import _blocks
from . import authentik_api as A
from .httpapi import Client, HttpError, location

SLUG = "orcahub"
SOURCE_NAME = "OrcaHub"
POLICY_NAME = "OrcaHub - Require verified email"
SOURCE_FLOWS = ("default-source-authentication", "default-source-enrollment")
REQUIRED = ("issuer", "authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri")


def callback(ctx) -> str:
    """The redirect OrcaHub registers for this deployment."""
    return f"{ctx.public_url('authentik')}/source/oauth/callback/{SLUG}/"


def discovery(ctx, url: str) -> Dict[str, Any]:
    """The upstream discovery document, fetched from inside orca-authentik so the DNS and
    egress path are the ones Authentik itself will use at sign-in."""
    res = ctx.remote("authentik").run(f"curl -fsS --max-time 20 {shlex.quote(url)}", check=False, timeout=60)
    if not res.ok:
        raise InstallError(f"orca-authentik cannot fetch the OrcaHub discovery URL {url}: "
                           f"{(res.err or res.out).strip()[-200:]}",
                           remediation="Check the URL from OrcaHub and that orca-authentik has "
                                       "outbound HTTPS.")
    try:
        doc = json.loads(res.out)
    except ValueError:
        raise InstallError(f"{url} did not return a JSON discovery document.") from None
    missing = [k for k in REQUIRED if not doc.get(k)]
    if missing:
        raise InstallError(f"The OrcaHub discovery document lacks {', '.join(missing)}.")
    scopes = set(doc.get("scopes_supported") or ())
    if scopes and not {"openid", "email", "profile"} <= scopes:
        raise InstallError("The OrcaHub provider does not offer the openid, email and profile scopes "
                           f"(it offers {', '.join(sorted(scopes))}).")
    return doc


def _source_body(ctx, doc: Dict[str, Any], flows: Dict[str, str], enabled: bool) -> Dict[str, Any]:
    pkce = "S256" if "S256" in (doc.get("code_challenge_methods_supported") or ()) else "none"
    return {
        "name": SOURCE_NAME, "slug": SLUG, "enabled": enabled, "provider_type": "openidconnect",
        "consumer_key": ctx.values["ORCAHUB_CLIENT_ID"],
        "consumer_secret": ctx.secrets.get("orcahub_client_secret"),
        "oidc_well_known_url": ctx.values["ORCAHUB_DISCOVERY_URL"],
        "authorization_url": doc["authorization_endpoint"], "access_token_url": doc["token_endpoint"],
        "profile_url": doc["userinfo_endpoint"], "oidc_jwks_url": doc["jwks_uri"],
        "additional_scopes": "", "pkce": pkce,
        "user_matching_mode": "email_link",
        # note: name_deny refuses a sign-in whose upstream groups collide with a local group
        # instead of linking it, the fail-closed reading of the guide's "never link role groups"
        "group_matching_mode": "name_deny",
        "authentication_flow": flows["default-source-authentication"],
        "enrollment_flow": flows["default-source-enrollment"],
    }


def _source(c: Client) -> Optional[Dict[str, Any]]:
    try:
        return c.get(f"/sources/oauth/{SLUG}/")
    except HttpError as exc:
        if exc.status == 404:
            return None
        raise


def _bind_gate(c: Client, policy_pk: str, flow: Dict[str, Any]) -> List[str]:
    """Engine mode ALL on the flow and the gate bound at order 10, enabled, not negated, deny on
    failure (a binding someone switched off or negated is put back). Returns the names of
    other bindings the operator should review."""
    if flow.get("policy_engine_mode") != "all":
        c.patch(f"/flows/instances/{flow['slug']}/", {"policy_engine_mode": "all"})
    target = flow["policybindingmodel_ptr_id"]
    rows = (c.get(f"/policies/bindings/?{urllib.parse.urlencode({'target': target})}") or {}).get("results", [])
    want = {"policy": policy_pk, "target": target, "order": 10, "enabled": True, "negate": False,
            "failure_result": False, "timeout": 30}
    ours = [r for r in rows if r.get("policy") == policy_pk]
    if not ours:
        c.post("/policies/bindings/", want)
    for r in ours:
        if (r.get("enabled"), r.get("negate"), r.get("failure_result")) != (True, False, False):
            c.patch(f"/policies/bindings/{r['pk']}/", want)
    others = []
    for r in rows:
        name = (r.get("policy_obj") or {}).get("name") or r.get("group") or r.get("user")
        if r.get("policy") != policy_pk and name != f"{flow['slug']}-if-sso":
            others.append(str(name))
    return others


def _login_stage(c: Client) -> Dict[str, Any]:
    """The identification stage of the active brand's authentication flow."""
    brands = (c.get("/core/brands/") or {}).get("results", [])
    brand = next((b for b in brands if b.get("default")), brands[0] if brands else {})
    flow_pk = brand.get("flow_authentication")
    flow = None
    if flow_pk:
        flow = next((f for f in (c.get(f"/flows/instances/?{urllib.parse.urlencode({'pk': flow_pk})}") or {})
                     .get("results", []) if f.get("pk") == flow_pk), None)
    flow = flow or A._blueprint_object(c, "/flows/instances/", "flow default-authentication-flow",
                                       slug="default-authentication-flow")
    bindings = (c.get(f"/flows/bindings/?{urllib.parse.urlencode({'target': flow['pk'], 'ordering': 'order'})}")
                or {}).get("results", [])
    for b in bindings:
        if (b.get("stage_obj") or {}).get("component") == "ak-stage-identification-form":
            return c.get(f"/stages/identification/{b['stage']}/")
    raise InstallError(f"The authentication flow {flow['slug']} has no identification stage to put "
                       "the OrcaHub button on.")


def configure(ctx, token: str) -> Dict[str, Any]:
    c = A.api(ctx, token)
    doc = discovery(ctx, ctx.values["ORCAHUB_DISCOVERY_URL"])
    flows = {slug: A._blueprint_object(c, "/flows/instances/", f"flow {slug}", slug=slug) for slug in SOURCE_FLOWS}
    # important: the gate is bound before the source is enabled. A source live without it
    # would link an unverified email to the account holding it, akadmin included.
    policy = A._upsert(c, "/policies/expression/", {"name": POLICY_NAME},
                       {"name": POLICY_NAME, "expression": _blocks.ORCAHUB_POLICY, "execution_logging": False})
    review = []
    for flow in flows.values():
        review += _bind_gate(c, policy["pk"], flow)
    body = _source_body(ctx, doc, {s: f["pk"] for s, f in flows.items()}, enabled=True)
    source = c.patch(f"/sources/oauth/{SLUG}/", body) if _source(c) else c.post("/sources/oauth/", body)
    stage = _login_stage(c)
    sources = list(stage.get("sources") or [])
    if source["pk"] not in sources or not stage.get("show_source_labels"):
        # user_fields again: the stage validates a PATCH as if it were the whole object
        c.patch(f"/stages/identification/{stage['pk']}/",
                {"sources": sources + ([source["pk"]] if source["pk"] not in sources else []),
                 "user_fields": stage.get("user_fields") or [], "show_source_labels": True})
    return {"pkce": body["pkce"], "issuer": doc["issuer"], "authorize": doc["authorization_endpoint"],
            "stage": stage["name"], "review": review}


def disable(ctx, token: str) -> bool:
    """Take the button away and turn the source off. The policy and its bindings stay: they
    only ever judge the orcahub source, so they are inert without it."""
    c = A.api(ctx, token)
    source = _source(c)
    if source is None:
        return False
    stage = _login_stage(c)
    if source["pk"] in (stage.get("sources") or []):
        c.patch(f"/stages/identification/{stage['pk']}/",
                {"sources": [s for s in stage["sources"] if s != source["pk"]],
                 "user_fields": stage.get("user_fields") or []})
    # note: Authentik 2025.10 validates every source PATCH as a full body (a partial one fails
    # with KeyError provider_type, answered as 405) and refetches the upstream discovery on
    # each save, so switching off goes through the ORM and works while OrcaHub is unreachable
    A.shell(ctx, "import json\nfrom authentik.sources.oauth.models import OAuthSource\n"
                 f"n = OAuthSource.objects.filter(slug={json.dumps(SLUG)}).update(enabled=False)\n"
                 'print("ORCASTRA_RESULT " + json.dumps({"updated": n}))\n')
    return True


def offered(ctx) -> List[str]:
    """The sources the public login page offers right now."""
    challenge = Client(ctx.public_url("authentik"), timeout=20).get(
        "/api/v3/flows/executor/default-authentication-flow/?query=") or {}
    return [s.get("name") for s in challenge.get("sources") or []]


def prove(ctx, authorize_url: str) -> List[str]:
    """From the outside, as a browser: the login page offers OrcaHub, and its button sends the
    browser to OrcaHub's authorize endpoint with our client id and exactly the callback
    OrcaHub registered. Returns what is wrong (empty = proven)."""
    problems = []
    base = ctx.public_url("authentik")
    try:
        names = offered(ctx)
        if SOURCE_NAME not in names:
            problems.append(f"the login page offers {names or 'no sources'}, not {SOURCE_NAME}")
    except HttpError as exc:
        problems.append(f"login flow: {exc.message[:160]}")
    try:
        status, target = location(f"{base}/source/oauth/login/{SLUG}/")
    except HttpError as exc:
        return problems + [f"the OrcaHub button: {exc.message[:160]}"]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(target).query)
    if status not in (301, 302, 303, 307) or not target.startswith(authorize_url):
        problems.append(f"the OrcaHub button answered HTTP {status} {target[:120]}")
    elif query.get("client_id") != [ctx.values["ORCAHUB_CLIENT_ID"]]:
        problems.append("the OrcaHub button sends another client id")
    elif query.get("redirect_uri") != [callback(ctx)]:
        problems.append(f"the OrcaHub button sends redirect_uri {query.get('redirect_uri')}, "
                        f"OrcaHub registered {callback(ctx)}")
    return problems
