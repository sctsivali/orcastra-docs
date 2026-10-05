"""Application checks from where real traffic comes from: the public URLs (through the LXD
forwards), the NextAuth sign-in redirect (proves the frontend reaches the issuer through
the hairpin), and calls from inside the CMP containers to Vault, the Authentik API and the
issuer's JWKS with the credentials the containers actually hold."""
from __future__ import annotations

import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from . import authentik_api as A
from . import topology as T
from .httpapi import Client, HttpError

Check = Callable[[str, bool, str], None]
FRONTEND = "orcastra-dashboard-frontend"
BACKEND = "orcastra-dashboard-backend"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):  # noqa: ANN002 - urllib signature
        return None


def _status(url: str) -> int:
    parts = urllib.parse.urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    try:
        return Client(base, timeout=15).request("GET", parts.path or "/", ok=tuple(range(200, 600)))[0]
    except HttpError as exc:
        return exc.status


def public_urls(ctx, check: Check) -> None:
    cmp, api = ctx.public_url("cmp"), ctx.public_url("api")
    check(f"{cmp}/healthz", _status(cmp + "/healthz") == 200, str(_status(cmp + "/healthz")))
    try:
        body = Client(api, timeout=15).get("/health") or {}
        check(f"{api}/health", body.get("status") == "healthy", str(body.get("status")))
    except HttpError as exc:
        check(f"{api}/health", False, str(exc))
    auth = ctx.public_url("authentik")
    check(f"{auth}/-/health/live/", _status(auth + "/-/health/live/") in (200, 204),
          str(_status(auth + "/-/health/live/")))
    logs = ctx.public_url("logs")
    check(f"{logs}/api/status", _status(logs + "/api/status") in (200, 401), str(_status(logs + "/api/status")))
    try:
        doc = Client(auth, timeout=15).get(f"/application/o/{T.ISSUER_SLUG}/.well-known/openid-configuration")
        check("issuer through the public URL", doc.get("issuer") == ctx.issuer, str(doc.get("issuer")))
    except HttpError as exc:
        check("issuer through the public URL", False, str(exc))


def signin_redirect(ctx, check: Check) -> None:
    """GET csrf, POST signin/authentik, expect a redirect to Authentik's authorize endpoint
    on the public address with our client_id."""
    base = ctx.public_url("cmp")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), _NoRedirect)
    try:
        with opener.open(base + "/api/auth/csrf", timeout=20) as resp:
            csrf = json.loads(resp.read().decode())["csrfToken"]
        data = urllib.parse.urlencode({"csrfToken": csrf, "callbackUrl": base + "/", "json": "true"}).encode()
        req = urllib.request.Request(base + "/api/auth/signin/authentik", data=data, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with opener.open(req, timeout=30) as resp:
                target = json.loads(resp.read().decode() or "{}").get("url", "")
        except urllib.error.HTTPError as exc:
            target = exc.headers.get("Location", "")
    except (OSError, ValueError, KeyError) as exc:
        check("sign-in redirects to Authentik", False, str(exc)[:200])
        return
    want = ctx.public_url("authentik") + "/application/o/authorize/"
    ok = target.startswith(want) and urllib.parse.quote(ctx.secrets.get("oidc_client_id")) in target
    check("sign-in redirects to Authentik authorize (frontend reached the issuer)", ok, target[:160])


_PY = ("import json,os,urllib.request as u;"
       "r=u.Request({url},headers={headers});"
       "print(json.dumps(json.load(u.urlopen(r,timeout=10))))")


def _in_container(ctx, container: str, url_expr: str, headers_expr: str = "{}") -> dict:
    code = _PY.format(url=url_expr, headers=headers_expr)
    res = ctx.remote("cmp").run(f"docker exec {container} python3 -c {json.dumps(code)}",
                                check=False, timeout=60)
    if not res.ok:
        raise RuntimeError((res.err or res.out).strip()[-200:])
    return json.loads(res.out.strip().splitlines()[-1])


def from_containers(ctx, check: Check) -> None:
    try:
        d = _in_container(ctx, BACKEND, "os.environ['VAULT_ADDR']+'/v1/auth/token/lookup-self'",
                          "{'X-Vault-Token':os.environ['VAULT_TOKEN']}")
        check("backend -> Vault with its token", "orcastra-policy" in d["data"]["policies"],
              d["data"].get("display_name", ""))
    except Exception as exc:  # noqa: BLE001 - reported as a failed check
        check("backend -> Vault with its token", False, str(exc))
    try:
        d = _in_container(ctx, BACKEND,
                          "os.environ['AUTHENTIK_API_URL']+'/api/v3/core/groups/?name=role_admin'",
                          "{'Authorization':'Bearer '+os.environ['AUTHENTIK_API_TOKEN']}")
        check("backend -> Authentik API with the role-sync token", d.get("results", [{}])[0].get("name") == "role_admin",
              str(len(d.get("results", []))) + " result(s)")
    except Exception as exc:  # noqa: BLE001
        check("backend -> Authentik API with the role-sync token", False, str(exc))
    try:
        d = _in_container(ctx, BACKEND, "os.environ['AUTHENTIK_ISSUER']+'jwks/'")
        check("backend -> issuer JWKS through the hairpin", len(d.get("keys", [])) > 0,
              f"{len(d.get('keys', []))} key(s)")
    except Exception as exc:  # noqa: BLE001
        check("backend -> issuer JWKS through the hairpin", False, str(exc))
    res = ctx.remote("cmp").run(
        f"docker exec {FRONTEND} node -e \"fetch(process.env.AUTHENTIK_ISSUER+'.well-known/openid-configuration')"
        ".then(r=>r.json()).then(j=>console.log(j.issuer)).catch(e=>{{console.error(e.message);process.exit(1)}})\"",
        check=False, timeout=60)
    check("frontend -> issuer discovery through the hairpin", res.ok and res.out.strip() == ctx.issuer,
          (res.out or res.err).strip()[-160:])
    res = ctx.remote("cmp").run("docker exec orcastra-dashboard-fluent-bit curl -sf http://127.0.0.1:2020/api/v1/health",
                                check=False, timeout=30)
    check("CMP Fluent Bit healthy", res.ok, res.out.strip()[:60])


def all_checks(ctx, check: Check) -> None:
    public_urls(ctx, check)
    signin_redirect(ctx, check)
    from_containers(ctx, check)
