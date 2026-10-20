"""Public HTTPS domain (Cloudflare Tunnel), OrcaHub federation, deletion protection and the host
tool refresh in the Full installer.

Run:  cd installer && python3 -m unittest discover -s tests -v
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from _fakes import FakeCtx, FakeLxd
from orcastra_core.errors import ConfigError
from orcastra_core.proc import Result
from orcastra_full_install import cloudflare, config as C, httpapi, orcahub as O, os_render
from orcastra_full_install import topology as T
from orcastra_full_install.context import FullContext
from orcastra_full_install.phases import p02_wizard, p05_instances, p12b_tunnel, p15_watchdog

# synthetic: decodes to a made-up account, tunnel 00000000-1111-4222-8333-444444444444
TOKEN = "eyJhIjoiMDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWYiLCJ0IjoiMDAwMDAwMDAtMTExMS00MjIyLTgzMzMtNDQ0NDQ0NDQ0NDQ0IiwicyI6ImMzbHVkR2hsZEdsakxYUmxjM1F0YjI1c2VRPT0ifQ"
DOMAIN = {"PUBLIC_DOMAIN": "example.com", "DOMAIN_CMP": "app.example.com", "DOMAIN_API": "api.example.com",
          "DOMAIN_SSO": "sso.example.com", "DOMAIN_LOGS": "logs.example.com"}
BASE = {"HOST_ADDRESS": "192.168.20.143", "PORT_AUTHENTIK": "9000", "PORT_CMP": "4321",
        "PORT_API": "8765", "PORT_LOGS": "5601"}


def full_ctx(values):
    d = tempfile.mkdtemp()
    log = mock.Mock()
    return FullContext(deploy_dir=d, flags=None, log=log, proc=None, state=None, prompt=None,
                       interactive=False, dry_run=True, values=dict(values))


class DomainValues(unittest.TestCase):
    def test_tunnel_token_from_a_pasted_install_command(self):
        for pasted in (TOKEN, f"sudo cloudflared service install {TOKEN}",
                       f"cloudflared tunnel run --token {TOKEN}\n"):
            self.assertEqual(C.tunnel_token(pasted), TOKEN)
            self.assertIsNone(C.v_tunnel_token(pasted))
        self.assertEqual(C.tunnel_id(TOKEN), "00000000-1111-4222-8333-444444444444")
        for bad in ("", "eyJabc", "not a token", "eyJ" + "A" * 40):
            self.assertIsNotNone(C.v_tunnel_token(bad))

    def test_hostnames_and_urls(self):
        for ok in ("app.example.com", "lab-sso.fossgo.id", "cmp.partner.co.id"):
            self.assertIsNone(C.v_fqdn(ok))
        for bad in ("App.example.com", "localhost", "10.0.0.1", "-a.example.com", "a..b.com"):
            self.assertIsNotNone(C.v_fqdn(bad))
        self.assertIsNone(C.v_https_url("https://sso.orcastra.io/application/o/x/.well-known/openid-configuration"))
        self.assertIsNotNone(C.v_https_url("http://sso.orcastra.io/x"))
        for off in ("", None, "none", "OFF", " off "):
            self.assertTrue(C.is_off(off))
        self.assertFalse(C.is_off("example.com"))

    def test_secrets_never_become_flags_or_state(self):
        from orcastra_full_install.cli import build_parser
        flags = build_parser().format_help()
        for key in C.SECRET_KEYS:
            self.assertNotIn("--" + key.lower().replace("_", "-"), flags)

    def test_public_urls_follow_domain_mode(self):
        ip = full_ctx(BASE)
        self.assertFalse(ip.domain_mode)
        self.assertEqual(ip.public_url("cmp"), "http://192.168.20.143:4321")
        dom = full_ctx({**BASE, **DOMAIN})
        self.assertTrue(dom.domain_mode)
        self.assertEqual(dom.public_url("cmp"), "https://app.example.com")
        self.assertEqual(dom.public_url("api"), "https://api.example.com")
        self.assertEqual(dom.issuer, "https://sso.example.com/application/o/orcastra-dashboard/")

    def test_dashboards_cookie_follows_https(self):
        self.assertIn("opensearch_security.cookie.secure: true", os_render.dashboards_yml(https=True))
        self.assertIn("opensearch_security.cookie.secure: false", os_render.dashboards_yml(https=False))

    def test_rerender_sees_a_domain_added_later_but_not_a_new_installer(self):
        rc3 = dict(BASE)                         # an RC3 deployment never had the new keys
        self.assertEqual(C.rerender_keys(rc3, {**BASE, "PUBLIC_DOMAIN": "", "ORCAHUB_CLIENT_ID": ""}), [])
        keys = C.rerender_keys(rc3, {**BASE, **DOMAIN})
        self.assertEqual(sorted(keys), sorted(DOMAIN))
        phases = C.rerender_phases(keys)
        for p in ("authentik", "opensearch", "cmp", "verify", "summary"):
            self.assertIn(p, phases)
        self.assertIn("firewall", C.rerender_phases(C.rerender_keys(BASE, {**BASE, "HOST_ADDRESS": "10.0.0.5"})))


class DomainWizard(unittest.TestCase):
    def _ctx(self, values, answered=(), interactive=False, confirm=False, secret="", secrets=None):
        ctx = FakeCtx("m3", FakeLxd(), values=values, answered=answered, interactive=interactive)
        ctx.prompt.confirm_answer = confirm
        ctx.prompt.secret = secret
        ctx.secrets.data.update(secrets or {})
        return ctx

    def test_off_unless_asked_for(self):
        ctx = self._ctx({"PUBLIC_DOMAIN": "", "DOMAIN_CMP": "stale.example.com"}, interactive=True)
        p02_wizard._domain(ctx)
        self.assertEqual(ctx.values["PUBLIC_DOMAIN"], "")
        self.assertNotIn("DOMAIN_CMP", ctx.values)

    def test_unattended_domain_takes_default_hostnames_and_the_token(self):
        ctx = self._ctx({"PUBLIC_DOMAIN": "example.com", "CLOUDFLARE_TUNNEL_TOKEN": f"cloudflared tunnel run --token {TOKEN}"},
                        answered={"PUBLIC_DOMAIN", "CLOUDFLARE_TUNNEL_TOKEN"})
        p02_wizard._domain(ctx)
        self.assertEqual([ctx.values[k] for k, _ in T.DOMAIN_KEYS.values()],
                         ["app.example.com", "api.example.com", "sso.example.com", "logs.example.com"])
        self.assertEqual(ctx.values["CLOUDFLARE_TUNNEL_TOKEN"], TOKEN)
        self.assertEqual(ctx.facts["tunnel_id"], "00000000-1111-4222-8333-444444444444")

    def test_unattended_domain_without_a_token_fails(self):
        ctx = self._ctx({"PUBLIC_DOMAIN": "example.com"}, answered={"PUBLIC_DOMAIN"})
        with self.assertRaises(ConfigError):
            p02_wizard._domain(ctx)

    def test_stored_token_is_reused_and_interactive_asks_hidden(self):
        ctx = self._ctx({"PUBLIC_DOMAIN": "example.com"}, answered={"PUBLIC_DOMAIN"},
                        secrets={"cloudflare_tunnel_token": TOKEN})
        p02_wizard._domain(ctx)
        self.assertEqual(ctx.prompt.asked_secret, [])
        ctx = self._ctx({"PUBLIC_DOMAIN": ""}, interactive=True, confirm=True, secret=TOKEN)
        ctx.prompt.answers["public-domain"] = "example.com"
        p02_wizard._domain(ctx)
        self.assertEqual(len(ctx.prompt.asked_secret), 1)
        self.assertEqual(ctx.values["DOMAIN_SSO"], "sso.example.com")

    def test_duplicate_hostnames_are_refused(self):
        ctx = self._ctx({"PUBLIC_DOMAIN": "example.com", "DOMAIN_CMP": "x.example.com",
                         "DOMAIN_API": "x.example.com", "CLOUDFLARE_TUNNEL_TOKEN": TOKEN},
                        answered={"PUBLIC_DOMAIN", "DOMAIN_CMP", "DOMAIN_API", "CLOUDFLARE_TUNNEL_TOKEN"})
        with self.assertRaises(ConfigError):
            p02_wizard._domain(ctx)

    def test_orcahub_needs_the_domain_and_its_secret(self):
        ctx = self._ctx({"ORCAHUB_CLIENT_ID": "abc", "ORCAHUB_DISCOVERY_URL": "https://h/.well-known/openid-configuration"},
                        answered={"ORCAHUB_CLIENT_ID", "ORCAHUB_DISCOVERY_URL"})
        with self.assertRaises(ConfigError) as cm:
            p02_wizard.orcahub_settings(ctx)
        self.assertIn("PUBLIC_DOMAIN", cm.exception.remediation)
        ctx.state.artifacts["orcahub"] = {"client_id": "abc"}
        with self.assertRaises(ConfigError) as cm:
            p02_wizard.orcahub_settings(ctx)
        self.assertIn("orcahub --disable", cm.exception.remediation)
        ctx = self._ctx({**DOMAIN, "ORCAHUB_CLIENT_ID": "abc", "ORCAHUB_DISCOVERY_URL": "https://h/.well-known/openid-configuration"},
                        answered={"ORCAHUB_CLIENT_ID", "ORCAHUB_DISCOVERY_URL"})
        with self.assertRaises(ConfigError):
            p02_wizard.orcahub_settings(ctx)          # no secret anywhere, unattended
        ctx.secrets.data["orcahub_client_secret"] = "s3cret"
        p02_wizard.orcahub_settings(ctx)
        self.assertEqual(ctx.values["ORCAHUB_CLIENT_SECRET"], "s3cret")

    def test_orcahub_off_clears_both_values_and_the_command_asks_again(self):
        ctx = self._ctx({**DOMAIN, "ORCAHUB_CLIENT_ID": "none", "ORCAHUB_DISCOVERY_URL": "x"},
                        answered={"ORCAHUB_CLIENT_ID"})
        p02_wizard.orcahub_settings(ctx)
        self.assertEqual((ctx.values["ORCAHUB_CLIENT_ID"], ctx.values["ORCAHUB_DISCOVERY_URL"]), ("", ""))
        url = "https://idp.example.com/application/o/p/.well-known/openid-configuration"
        ctx = self._ctx({**DOMAIN}, interactive=True, secret="fresh", secrets={"orcahub_client_secret": "old"})
        ctx.prompt.answers.update({"orcahub-client-id": "cid", "orcahub-discovery-url": url})
        p02_wizard.orcahub_settings(ctx, force=True)
        self.assertEqual(ctx.values["ORCAHUB_CLIENT_SECRET"], "fresh")   # force asks, never reuses


class FakeAuthentik:
    """Answers the Authentik API paths the federation code uses and records every write."""

    def __init__(self, source=None, stage_sources=(), extra_binding=None, our_binding=None):
        self.writes = []
        self.our_binding = our_binding
        self.source = source
        self.flows = {s: {"pk": f"pk-{s}", "slug": s, "policy_engine_mode": "any",
                          "policybindingmodel_ptr_id": f"pbm-{s}"} for s in O.SOURCE_FLOWS}
        self.flows["default-authentication-flow"] = {"pk": "pk-auth", "slug": "default-authentication-flow"}
        self.stage = {"pk": "stage-1", "name": "default-authentication-identification",
                      "sources": list(stage_sources), "user_fields": ["email", "username"],
                      "show_source_labels": False}
        self.extra_binding = extra_binding

    def get(self, path):
        if path.startswith(f"/sources/oauth/{O.SLUG}/"):
            if self.source is None:
                raise httpapi.HttpError(404, "", path)
            return self.source
        if path.startswith("/flows/instances/?"):
            slug = path.split("slug=")[-1] if "slug=" in path else None
            rows = [f for f in self.flows.values() if slug is None or f["slug"] == slug]
            if "pk=" in path:
                rows = [f for f in self.flows.values() if f["pk"] == path.split("pk=")[-1]]
            return {"results": rows}
        if path.startswith("/policies/expression/"):
            return {"results": []}
        if path.startswith("/policies/bindings/"):
            rows = [{"policy": "p-sso", "policy_obj": {"name": f"{s}-if-sso"}} for s in O.SOURCE_FLOWS
                    if f"pbm-{s}" in path]
            if self.extra_binding and "default-source-authentication" in path:
                rows.append({"policy": "p-extra", "policy_obj": {"name": self.extra_binding}})
            if self.our_binding:
                rows.append(dict(self.our_binding, pk=f"b-{path[-30:]}", policy="pol-1"))
            return {"results": rows}
        if path == "/core/brands/":
            return {"results": [{"default": True, "flow_authentication": "pk-auth"}]}
        if path.startswith("/flows/bindings/"):
            return {"results": [{"stage": "stage-1", "stage_obj": {"component": "ak-stage-identification-form"}}]}
        if path.startswith("/stages/identification/"):
            return self.stage
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path, body):
        self.writes.append(("POST", path, body))
        if path == "/sources/oauth/":
            self.source = dict(body, pk="src-1")
            return self.source
        if path == "/policies/expression/":
            return dict(body, pk="pol-1")
        return {}

    def patch(self, path, body):
        self.writes.append(("PATCH", path, body))
        if path.startswith("/sources/oauth/"):
            self.source = dict(self.source or {}, **body)
            return self.source
        return {}


DISCOVERY = {"issuer": "https://idp/application/o/x/", "authorization_endpoint": "https://idp/authorize/",
             "token_endpoint": "https://idp/token/", "userinfo_endpoint": "https://idp/userinfo/",
             "jwks_uri": "https://idp/jwks/", "scopes_supported": ["openid", "email", "profile"],
             "code_challenge_methods_supported": ["plain", "S256"]}


class Federation(unittest.TestCase):
    def _ctx(self):
        ctx = FakeCtx("m3", FakeLxd(), values={**BASE, **DOMAIN, "ORCAHUB_CLIENT_ID": "cid",
                                               "ORCAHUB_DISCOVERY_URL": "https://idp/.well-known/openid-configuration"})
        ctx.secrets.data["orcahub_client_secret"] = "csecret"
        ctx.public_url = lambda svc: "https://sso.example.com"
        return ctx

    def _configure(self, fake, doc=DISCOVERY):
        ctx = self._ctx()
        with mock.patch.object(O.A, "api", return_value=fake), \
                mock.patch.object(O, "discovery", return_value=doc):
            return ctx, O.configure(ctx, "token")

    def test_source_links_by_verified_email_and_never_links_groups(self):
        fake = FakeAuthentik()
        _, out = self._configure(fake)
        body = next(b for m, p, b in fake.writes if p == "/sources/oauth/")
        self.assertEqual((body["slug"], body["provider_type"], body["user_matching_mode"], body["group_matching_mode"]),
                         ("orcahub", "openidconnect", "email_link", "name_deny"))
        self.assertEqual((body["consumer_key"], body["consumer_secret"]), ("cid", "csecret"))
        self.assertEqual(body["authentication_flow"], "pk-default-source-authentication")
        self.assertEqual(body["enrollment_flow"], "pk-default-source-enrollment")
        self.assertEqual(out["pkce"], "S256")
        _, out = self._configure(FakeAuthentik(), dict(DISCOVERY, code_challenge_methods_supported=["plain"]))
        self.assertEqual(out["pkce"], "none")

    def test_gate_is_the_guide_policy_bound_to_both_flows_in_all_mode(self):
        fake = FakeAuthentik()
        self._configure(fake)
        policy = next(b for m, p, b in fake.writes if p == "/policies/expression/")
        self.assertIn('userinfo.get("email_verified") is True', policy["expression"])
        self.assertIn('!= "orcahub"', policy["expression"])
        modes = [(p, b) for m, p, b in fake.writes if p.startswith("/flows/instances/")]
        self.assertEqual(sorted(p for p, _ in modes), sorted(f"/flows/instances/{s}/" for s in O.SOURCE_FLOWS))
        self.assertTrue(all(b == {"policy_engine_mode": "all"} for _, b in modes))
        binds = [b for m, p, b in fake.writes if p == "/policies/bindings/"]
        self.assertEqual(sorted(b["target"] for b in binds), sorted(f"pbm-{s}" for s in O.SOURCE_FLOWS))
        self.assertTrue(all(b["order"] == 10 and b["failure_result"] is False and not b["negate"] for b in binds))

    def test_the_gate_is_bound_before_the_source_goes_live(self):
        fake = FakeAuthentik()
        self._configure(fake)
        paths = [p for m, p, b in fake.writes]
        source_at = paths.index("/sources/oauth/")
        self.assertTrue(all(paths.index(p) < source_at for p in paths
                            if p.startswith(("/policies/", "/flows/instances/"))))

    def test_a_switched_off_or_negated_gate_is_put_back(self):
        fake = FakeAuthentik(our_binding={"enabled": False, "negate": True, "failure_result": True})
        self._configure(fake)
        fixed = [b for m, p, b in fake.writes if m == "PATCH" and p.startswith("/policies/bindings/")]
        self.assertEqual(len(fixed), 2)
        self.assertTrue(all((b["enabled"], b["negate"], b["failure_result"]) == (True, False, False) for b in fixed))
        self.assertFalse(any(m == "POST" and p == "/policies/bindings/" for m, p, b in fake.writes))

    def test_the_guide_policy_refuses_upstream_groups(self):
        from orcastra_full_install import _blocks
        expr = _blocks.ORCAHUB_POLICY
        self.assertIn('if userinfo.get("groups"):', expr)
        self.assertLess(expr.index('userinfo.get("groups")'), expr.index('userinfo.get("email_verified")'))

    def test_button_keeps_the_stage_valid_and_reports_extra_bindings(self):
        fake = FakeAuthentik(extra_binding="custom-geo-policy")
        _, out = self._configure(fake)
        stage = next(b for m, p, b in fake.writes if p.startswith("/stages/identification/"))
        self.assertEqual(stage["sources"], ["src-1"])
        self.assertEqual(stage["user_fields"], ["email", "username"])
        self.assertTrue(stage["show_source_labels"])
        self.assertEqual(out["review"], ["custom-geo-policy"])

    def test_rerun_updates_in_place(self):
        fake = FakeAuthentik(source={"pk": "src-1", "slug": "orcahub", "enabled": True}, stage_sources=["src-1"])
        fake.stage["show_source_labels"] = True
        self._configure(fake)
        self.assertFalse(any(p == "/sources/oauth/" for m, p, b in fake.writes))
        self.assertTrue(any(m == "PATCH" and p == "/sources/oauth/orcahub/" for m, p, b in fake.writes))
        self.assertFalse(any(p.startswith("/stages/identification/") for m, p, b in fake.writes))

    def test_disable_removes_the_button_and_switches_the_source_off_in_the_orm(self):
        fake = FakeAuthentik(source={"pk": "src-1", "slug": "orcahub", "enabled": True},
                             stage_sources=["src-1", "other"])
        ctx = self._ctx()
        with mock.patch.object(O.A, "api", return_value=fake), mock.patch.object(O.A, "shell") as shell:
            self.assertTrue(O.disable(ctx, "token"))
        stage = next(b for m, p, b in fake.writes if p.startswith("/stages/identification/"))
        self.assertEqual(stage, {"sources": ["other"], "user_fields": ["email", "username"]})
        self.assertFalse(any(p.startswith("/sources/oauth/") for m, p, b in fake.writes))
        self.assertIn('slug="orcahub"', shell.call_args[0][1])
        self.assertIn("update(enabled=False)", shell.call_args[0][1])

    def test_prove_checks_client_and_exact_callback(self):
        ctx = self._ctx()
        good = ("https://idp/authorize/?client_id=cid&redirect_uri="
                "https%3A%2F%2Fsso.example.com%2Fsource%2Foauth%2Fcallback%2Forcahub%2F&state=s")
        with mock.patch.object(O, "offered", return_value=["OrcaHub"]), \
                mock.patch.object(O, "location", return_value=(302, good)):
            self.assertEqual(O.prove(ctx, "https://idp/authorize/"), [])
        with mock.patch.object(O, "offered", return_value=[]), \
                mock.patch.object(O, "location", return_value=(302, good.replace("cid", "other"))):
            problems = O.prove(ctx, "https://idp/authorize/")
        self.assertEqual(len(problems), 2)
        self.assertIn("another client id", problems[1])


class TunnelConnector(unittest.TestCase):
    def test_unit_keeps_the_token_out_of_the_command_line(self):
        unit = p12b_tunnel.unit(FakeCtx())
        self.assertIn(f"EnvironmentFile={p12b_tunnel.ENV_PATH}", unit)
        self.assertIn("--no-autoupdate", unit)
        self.assertIn("--metrics 127.0.0.1:", unit)
        self.assertNotIn("--token", unit)

    def test_connector_is_sandboxed_away_from_vault_the_host_and_docker(self):
        unit = p12b_tunnel.unit(FakeCtx())
        for line in ("DynamicUser=yes", "CapabilityBoundingSet=\n", "ProtectSystem=strict",
                     "IPAddressDeny=10.77.0.11/32 10.77.0.1/32"):
            self.assertIn(line, unit)
        inaccessible = next(l for l in unit.splitlines() if l.startswith("InaccessiblePaths="))
        for path in ("/var/run/docker.sock", "/opt/orcastra", "/etc/orcastra"):
            self.assertIn(path, inaccessible)

    def test_authentik_trusts_forwarded_headers_from_the_connector_and_this_host_only(self):
        from orcastra_full_install.phases import p10_authentik
        ctx = FakeCtx()
        ctx.secrets.data.update({"authentik_pg_pass": "p", "authentik_secret_key": "k"})
        env = p10_authentik._env(ctx)
        self.assertIn("AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS=127.0.0.0/8,::1/128,10.77.0.14/32,10.77.0.1/32\n", env)

    def test_binary_is_the_pinned_release_checked_by_sha256(self):
        scripts = []

        class R:
            def run(self, script, **kw):
                scripts.append(script)
                return Result(0, "amd64\n" if "dpkg --print" in script else "", "", [])
        self.assertEqual(p12b_tunnel._install_binary(R()), T.PINS["cloudflared"])
        install = scripts[-1]
        self.assertIn(f"/releases/download/{T.PINS['cloudflared']}/cloudflared-linux-amd64.deb", install)
        self.assertIn(T.CLOUDFLARED_DEB_SHA256["amd64"], install)
        self.assertIn("sha256sum -c", install)

    def test_routes_table_matches_where_the_connector_runs(self):
        ctx = FakeCtx(values={**DOMAIN})
        ctx.public_host = lambda svc: ctx.values[T.DOMAIN_KEYS[svc][0]]
        self.assertEqual(cloudflare.routes(ctx), [
            ("app.example.com", "http://localhost:4321", "CMP frontend"),
            ("api.example.com", "http://localhost:8765", "CMP API (WebSockets on)"),
            ("sso.example.com", "http://10.77.0.12:9000", "Authentik"),
            ("logs.example.com", "http://10.77.0.13:5601", "OpenSearch Dashboards")])

    def test_failures_are_explained_the_way_an_operator_fixes_them(self):
        e = cloudflare._explain
        self.assertIn("published application route", e(0, "[Errno -2] Name or service not known"))
        self.assertIn("one level below the zone", e(0, "certificate verify failed"))
        self.assertIn("Browser Integrity Check", e(403, "error code: 1010"))
        self.assertIn("route is missing", e(404, ""))
        self.assertIn("connector is not connected", e(530, "error code: 1033"))

    def test_every_client_sends_an_explicit_user_agent(self):
        self.assertEqual(httpapi.Client("https://x").headers["User-Agent"], httpapi.USER_AGENT)
        self.assertNotIn("Python-urllib", httpapi.USER_AGENT)


class Protection(unittest.TestCase):
    def test_new_instances_are_protected_from_deletion(self):
        ctx = FakeCtx(values={"IMAGE": "ubuntu:24.04"})
        p05_instances._create(ctx, "vault")
        self.assertIn(f"{T.PROTECT_KEY}=true", ctx.lxd.calls[0])

    def test_older_instances_are_protected_unless_that_needs_a_vm_stop(self):
        ctx = FakeCtx()
        p05_instances._ensure_protection(ctx, "orca-vault", {"type": "container", "status": "Running", "config": {}})
        self.assertEqual(ctx.lxd.calls, [("config", "set", "orca-vault", f"{T.PROTECT_KEY}=true")])
        ctx = FakeCtx()
        p05_instances._ensure_protection(ctx, "orca-vault", {"type": "virtual-machine", "status": "Running", "config": {}})
        self.assertEqual(ctx.lxd.calls, [])
        self.assertTrue(any("lxc stop" in m for lvl, m in ctx.log.lines if lvl == "warn"))
        ctx = FakeCtx()
        p05_instances._ensure_protection(ctx, "orca-vault", {"type": "virtual-machine", "status": "Stopped", "config": {}})
        self.assertEqual(len(ctx.lxd.calls), 1)


class Uninstall(unittest.TestCase):
    def test_protected_instances_are_stopped_and_unprotected_before_delete(self):
        from orcastra_full_install.phases import uninstall

        class Lxd(FakeLxd):
            def instance(self, name):
                role = name.replace(T.PREFIX, "")
                status = "Stopped" if role == "vault" else "Running"
                return {"status": status, "config": {T.TAG_KEY: role, T.PROTECT_KEY: "true"}}

        ctx = FakeCtx(lxd=Lxd(), interactive=False)
        with mock.patch.object(uninstall, "_confirm"), \
                mock.patch.object(uninstall.p14_exposure, "remove_ours", side_effect=StopIteration):
            with self.assertRaises(StopIteration):
                uninstall.run(ctx)
        calls = ctx.lxd.calls
        self.assertEqual(calls[:2], [("config", "unset", "orca-vault", T.PROTECT_KEY),
                                     ("delete", "--force", "orca-vault")])
        i = calls.index(("stop", "--force", "orca-authentik"))
        self.assertEqual(calls[i + 1:i + 3], [("config", "unset", "orca-authentik", T.PROTECT_KEY),
                                              ("delete", "--force", "orca-authentik")])


class HostTool(unittest.TestCase):
    def test_tool_is_replaced_only_when_it_differs(self):
        with tempfile.TemporaryDirectory() as d:
            me, pyz = os.path.join(d, "new.pyz"), os.path.join(d, "installed.pyz")
            for path, body in ((me, b"new"), (pyz, b"old")):
                with open(path, "wb") as fh:
                    fh.write(body)
            paths = {"PYZ": pyz, "WRAPPER": os.path.join(d, "w"), "SERVICE": os.path.join(d, "s"),
                     "TIMER": os.path.join(d, "t")}
            ctx = mock.Mock()
            ctx.proc.run.return_value = Result(0, "", "", [])
            with mock.patch.multiple(p15_watchdog, **paths), mock.patch.object(p15_watchdog.sys, "argv", [me]):
                self.assertFalse(p15_watchdog._current(ctx))       # other archive
                with open(pyz, "wb") as fh:
                    fh.write(b"new")
                self.assertFalse(p15_watchdog._current(ctx))       # units missing
                for path, body in ((paths["WRAPPER"], p15_watchdog._WRAPPER.format(pyz=pyz)),
                                   (paths["SERVICE"], p15_watchdog._SERVICE.format(wrapper=paths["WRAPPER"])),
                                   (paths["TIMER"], p15_watchdog._TIMER)):
                    with open(path, "w") as fh:
                        fh.write(body)
                self.assertTrue(p15_watchdog._current(ctx))

    def test_phases_that_follow_settings_run_on_every_pass(self):
        from orcastra_full_install.phases import ALWAYS, PHASES
        names = [n for n, _ in PHASES]
        for p in ("tunnel", "orcahub", "watchdog"):
            self.assertIn(p, ALWAYS)
        self.assertLess(names.index("tunnel"), names.index("cmp"))
        self.assertLess(names.index("cmp"), names.index("orcahub"))
        self.assertEqual(json.dumps(names).count('"'), 2 * len(names))


if __name__ == "__main__":
    unittest.main()
