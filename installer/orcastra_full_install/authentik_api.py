"""Authentik configuration through its REST API (v3), the way an operator would click it in
docs/deployment/vm1-authentik.md, but repeatable.

Admin access per run comes from a short-lived akadmin token created with `ak shell` (the
Python code, including the token key and the akadmin password, travels on stdin only), so
no long-lived admin credential is ever written to orca-authentik's disk. The token is
deleted at the end of the phase.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from orcastra_core.errors import InstallError

from . import topology as T
from .httpapi import Client, HttpError

INSTALLER_TOKEN = "orcastra-installer"
API_TOKEN = "orcastra-dashboard-api"
SERVICE_USER = "orcastra-role-sync"
SERVICE_GROUP = "orcastra-service-accounts"
PROVIDER_NAME = "Orcastra Dashboard Provider"
APP_NAME = "Orcastra Dashboard"
SIGNING_KEY = "orcastra-oidc-signing"
ROLE_GROUPS = ("role_admin", "role_partner", "role_tenant")
SCOPES = ("openid", "email", "profile", "offline_access")
GLOBAL_PERMS = ("authentik_core.view_user", "authentik_core.view_group", "authentik_core.add_group")
GROUP_PERMS = ("authentik_core.add_user_to_group", "authentik_core.remove_user_from_group",
               "authentik_core.view_group")

_SHELL = '''
import json
from datetime import timedelta
from django.utils.timezone import now
from authentik.core.models import Token, TokenIntents, User
u = User.objects.filter(username="akadmin").first()
if u is None:
    print("ORCASTRA_RESULT " + json.dumps({{"error": "akadmin does not exist yet"}}))
else:
    changed = False
    if {set_password} and not u.check_password({password}):
        u.set_password({password}); changed = True
    if u.email != {email}:
        u.email = {email}; changed = True
    if changed:
        u.save()
    Token.objects.update_or_create(identifier={ident}, defaults=dict(
        user=u, intent=TokenIntents.INTENT_API, key={key}, expiring=True,
        expires=now() + timedelta(minutes=45), description="orcastra-full installer (temporary)"))
    print("ORCASTRA_RESULT " + json.dumps({{"ok": True, "password_changed": changed}}))
'''


def shell(ctx, code: str) -> Dict[str, Any]:
    res = ctx.remote("authentik").run(
        f"cd {T.REMOTE_DIR}/authentik && docker compose -p authentik exec -T worker ak shell",
        stdin=code, check=False, timeout=300)
    for line in res.out.splitlines():
        if line.startswith("ORCASTRA_RESULT "):
            return json.loads(line[len("ORCASTRA_RESULT "):])
    raise InstallError("`ak shell` in orca-authentik returned no result: "
                       + (res.err or res.out).strip()[-500:])


def admin_session(ctx, key: str, *, set_password: bool) -> Dict[str, Any]:
    code = _SHELL.format(set_password="True" if set_password else "False",
                         password=json.dumps(ctx.secrets.get("admin_password")),
                         email=json.dumps(ctx.values["ADMIN_EMAIL"]),
                         ident=json.dumps(INSTALLER_TOKEN), key=json.dumps(key))
    return shell(ctx, code)


def drop_admin_session(ctx) -> None:
    shell(ctx, f'from authentik.core.models import Token\n'
               f'Token.objects.filter(identifier={json.dumps(INSTALLER_TOKEN)}).delete()\n'
               f'print("ORCASTRA_RESULT {{}}")\n')


def api(ctx, token: str) -> Client:
    return Client(f"http://{ctx.ip('authentik')}:9000/api/v3",
                  headers={"Authorization": f"Bearer {token}"}, timeout=30)


def _one(c: Client, path: str, **params: str) -> Optional[Dict[str, Any]]:
    from urllib.parse import urlencode
    rows = (c.get(f"{path}?{urlencode(params)}") or {}).get("results", [])
    exact = [r for r in rows if all(str(r.get(k)) == v for k, v in params.items())]
    return exact[0] if exact else None


def _upsert(c: Client, path: str, key: Dict[str, str], body: Dict[str, Any], pk: str = "pk") -> Dict[str, Any]:
    row = _one(c, path, **key)
    if row is None:
        return c.post(path, body)
    return c.patch(f"{path}{row[pk]}/", body)


def configure(ctx, token: str) -> Dict[str, Any]:
    """Groups, provider, application and the role-sync service account. Returns pks."""
    c = api(ctx, token)
    groups = {}
    for g in ROLE_GROUPS:
        row = _one(c, "/core/groups/", name=g) or c.post("/core/groups/", {"name": g, "is_superuser": False})
        groups[g] = row["pk"]
    akadmin = _one(c, "/core/users/", username="akadmin")
    if akadmin and groups["role_admin"] not in akadmin.get("groups", []):
        c.request("POST", f"/core/groups/{groups['role_admin']}/add_user/", {"pk": akadmin["pk"]})

    flows = {}
    for field, slug in (("authorization_flow", "default-provider-authorization-implicit-consent"),
                        ("invalidation_flow", "default-provider-invalidation-flow")):
        row = _one(c, "/flows/instances/", slug=slug)
        if row is None:
            raise InstallError(f"Authentik flow {slug} is missing (blueprints not applied yet?)")
        flows[field] = row["pk"]
    mappings = []
    for scope in SCOPES:
        row = _one(c, "/propertymappings/provider/scope/",
                   managed=f"goauthentik.io/providers/oauth2/scope-{scope}")
        if row is None:
            raise InstallError(f"Authentik default scope mapping for {scope} is missing")
        mappings.append(row["pk"])
    key = _one(c, "/crypto/certificatekeypairs/", name=SIGNING_KEY)
    if key is None:
        key = c.post("/crypto/certificatekeypairs/generate/",
                     {"common_name": SIGNING_KEY, "subject_alt_name": "", "validity_days": 3650})

    callback = f"{ctx.public_url('cmp')}/api/auth/callback/authentik"
    provider = _upsert(c, "/providers/oauth2/", {"name": PROVIDER_NAME}, {
        "name": PROVIDER_NAME, **flows, "client_type": "confidential",
        "client_id": ctx.secrets.get("oidc_client_id"),
        "client_secret": ctx.secrets.get("oidc_client_secret"),
        "redirect_uris": [{"matching_mode": "strict", "url": callback}],
        "property_mappings": mappings, "signing_key": key["pk"],
        "sub_mode": "hashed_user_id", "include_claims_in_id_token": True,
        "issuer_mode": "per_provider",
    })
    _upsert(c, "/core/applications/", {"slug": T.ISSUER_SLUG}, {
        "name": APP_NAME, "slug": T.ISSUER_SLUG, "provider": provider["pk"],
        "meta_launch_url": ctx.public_url("cmp"), "policy_engine_mode": "any",
    }, pk="slug")
    return {"groups": groups, "provider": provider["pk"], "callback": callback}


def ensure_service_token(ctx, token: str, groups: Dict[str, str]) -> str:
    """A service account limited to reading users/groups and moving users between the three
    role groups (what backend/app/services/authentik_sync.py calls), with a non-expiring
    API token. The token key is stored on the host before it is used."""
    c = api(ctx, token)
    role = _one(c, "/rbac/roles/", name=SERVICE_USER) or c.post("/rbac/roles/", {"name": SERVICE_USER})
    group = _one(c, "/core/groups/", name=SERVICE_GROUP)
    if group is None:
        group = c.post("/core/groups/", {"name": SERVICE_GROUP, "is_superuser": False,
                                         "roles": [role["pk"]]})
    elif role["pk"] not in group.get("roles", []):
        group = c.patch(f"/core/groups/{group['pk']}/", {"roles": group.get("roles", []) + [role["pk"]]})
    user = _one(c, "/core/users/", username=SERVICE_USER)
    if user is None:
        user = c.post("/core/users/", {"username": SERVICE_USER, "name": "Orcastra role sync",
                                       "type": "service_account", "path": "orcastra",
                                       "is_active": True, "groups": [group["pk"]]})
    elif group["pk"] not in user.get("groups", []):
        c.request("POST", f"/core/groups/{group['pk']}/add_user/", {"pk": user["pk"]})

    rp = f"/rbac/permissions/assigned_by_roles/{role['pk']}/assign/"
    c.post(rp, {"permissions": list(GLOBAL_PERMS)})
    for g in groups.values():
        c.post(rp, {"permissions": list(GROUP_PERMS), "model": "authentik_core.group",
                    "object_pk": str(g)})

    existing = _one(c, "/core/tokens/", identifier=API_TOKEN)
    if existing is None:
        c.post("/core/tokens/", {"identifier": API_TOKEN, "intent": "api", "user": user["pk"],
                                 "expiring": False, "description": "Orcastra CMP role sync"})
    elif existing.get("expiring"):
        c.patch(f"/core/tokens/{API_TOKEN}/", {"expiring": False})
    key = c.get(f"/core/tokens/{API_TOKEN}/view_key/")["key"]
    ctx.secrets.put("authentik_api_token", key)
    return key


def prove_service_token(ctx, admin_token: str, svc_token: str, groups: Dict[str, str]) -> None:
    """Move a throwaway user into and out of role_tenant with the service token, exactly
    what the CMP does when an admin changes a role."""
    admin, svc = api(ctx, admin_token), api(ctx, svc_token)
    probe = _one(admin, "/core/users/", username="orcastra-selftest") or admin.post(
        "/core/users/", {"username": "orcastra-selftest", "name": "installer self-test",
                         "is_active": False, "path": "orcastra"})
    try:
        svc.get("/core/users/?search=orcastra-selftest")
        svc.request("POST", f"/core/groups/{groups['role_tenant']}/add_user/", {"pk": probe["pk"]})
        svc.request("POST", f"/core/groups/{groups['role_tenant']}/remove_user/", {"pk": probe["pk"]})
    except HttpError as exc:
        raise InstallError(f"The role-sync token cannot manage role groups: {exc}",
                           remediation="Check the orcastra-role-sync role permissions in Authentik.")
    finally:
        admin.request("DELETE", f"/core/users/{probe['pk']}/", ok=(204, 404))


def discovery(ctx) -> Dict[str, Any]:
    """The OIDC discovery document as a browser sees it (Host = public address)."""
    host = f"{ctx.host_address}:{ctx.ports['authentik']}"
    c = Client(f"http://{ctx.ip('authentik')}:9000", headers={"Host": host}, timeout=15)
    return c.get(f"/application/o/{T.ISSUER_SLUG}/.well-known/openid-configuration")


def jwks_count(ctx) -> int:
    host = f"{ctx.host_address}:{ctx.ports['authentik']}"
    c = Client(f"http://{ctx.ip('authentik')}:9000", headers={"Host": host}, timeout=15)
    return len((c.get(f"/application/o/{T.ISSUER_SLUG}/jwks/") or {}).get("keys", []))


def role_groups_of(ctx, token: str, username: str) -> List[str]:
    c = api(ctx, token)
    u = _one(c, "/core/users/", username=username)
    if not u:
        return []
    return [g.get("name") for g in u.get("groups_obj") or []]
