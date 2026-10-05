"""Unit tests for the Full installer: answers and validation, rendering of every file it
deploys, firewall rules, secrets formats, and the guarantees around secrets and state.

Run:  cd installer && python3 -m unittest discover -s tests -v
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

INSTALLER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, INSTALLER)
sys.path.insert(0, os.path.join(INSTALLER, "tools"))

import check_full_assets  # noqa: E402
from orcastra_core.errors import ConfigError, InstallError  # noqa: E402
from orcastra_full_install import (authentik_api, cloudinit, cmp_render, config as C,  # noqa: E402
                                   _blocks, bundled, firewall, os_render, pki, secrets,
                                   topology as T, wizard_lib as W)
from orcastra_full_install.cli import build_parser  # noqa: E402
from orcastra_full_install.remote import Remote  # noqa: E402

IPS = {"vault": "10.77.0.11", "authentik": "10.77.0.12", "opensearch": "10.77.0.13", "cmp": "10.77.0.14"}


class Merge(unittest.TestCase):
    def test_precedence_cli_answers_default(self):
        v = C.merge({"SIZING": "production", "IP_VAULT": None}, {"SIZING": "custom", "IP_VAULT": "10.0.0.5"})
        self.assertEqual(v["SIZING"], "production")
        self.assertEqual(v["IP_VAULT"], "10.0.0.5")
        self.assertEqual(v["PORT_CMP"], "4321")
        self.assertNotIn("IP_CMP", v)  # left for the wizard

    def test_unknown_answer_key_refused(self):
        with self.assertRaises(ConfigError):
            C.merge({}, {"IP_VALT": "1"})

    def test_frozen_changes(self):
        prev = {"IP_VAULT": "10.0.0.5", "HOST_ADDRESS": "1.1.1.1"}
        self.assertEqual(C.frozen_changes(prev, {"IP_VAULT": "10.0.0.6", "HOST_ADDRESS": "2.2.2.2"}),
                         ["IP_VAULT"])

    def test_admin_password_not_a_cli_flag(self):
        opts = [a for act in build_parser()._actions for a in act.option_strings]
        self.assertNotIn("--admin-password", opts)
        self.assertIn("--ip-vault", opts)


class Validators(unittest.TestCase):
    def test_subnet(self):
        self.assertIsNone(C.v_subnet("10.77.0.1/24"))
        self.assertIsNotNone(C.v_subnet("8.8.8.1/24"))     # public
        self.assertIsNotNone(C.v_subnet("10.77.0.0/24"))   # network address as gateway
        self.assertIsNotNone(C.v_subnet("10.77.0.1/30"))   # too small
        self.assertIsNotNone(C.v_subnet("nonsense"))

    def test_ip_in_subnet(self):
        chk = C.v_ip_in("10.77.0.1/24", "10.77.0.1")
        self.assertIsNone(chk("10.77.0.11"))
        self.assertIn("gateway", chk("10.77.0.1"))
        self.assertIn("outside", chk("10.78.0.11"))
        self.assertIn("broadcast", chk("10.77.0.255"))
        self.assertIsNotNone(chk("fe80::1"))

    def test_misc(self):
        self.assertIsNone(C.v_port("9000"))
        self.assertIsNotNone(C.v_port("70000"))
        self.assertIsNotNone(C.v_port("abc"))
        self.assertIsNone(C.v_email("a@b.co"))
        self.assertIsNotNone(C.v_email("nope"))
        self.assertIsNotNone(C.v_password("short"))
        self.assertIsNone(C.v_password("long-enough-pass"))
        self.assertIsNone(C.v_version(T.CMP_VERSION_DEFAULT))
        self.assertIsNotNone(C.v_version("9.9.9"))
        self.assertIsNotNone(C.v_name("Bad_Name"))

    def test_unique(self):
        with self.assertRaises(ConfigError):
            C.check_unique({"IP_A": "1", "IP_B": "1"}, ["IP_A", "IP_B"], "IP")

    def test_sizes_override(self):
        s = C.sizes_from({"SIZING": "compact", "MEM_OPENSEARCH": "8"})
        self.assertEqual(s["opensearch"]["mem"], 8)
        self.assertEqual(s["vault"], T.SIZING["compact"]["vault"])
        with self.assertRaises(ConfigError):
            C.sizes_from({"SIZING": "compact", "CPU_CMP": "zero"})


class Topology(unittest.TestCase):
    def test_heap(self):
        self.assertEqual(T.opensearch_heap_gib(16), 8)
        self.assertEqual(T.opensearch_heap_gib(6), 3)
        self.assertEqual(T.opensearch_heap_gib(1), 1)
        self.assertEqual(T.opensearch_heap_gib(128), 31)

    def test_backend_limits_fit_the_instance(self):
        for cpu, mem in ((2, 3), (2, 4), (4, 8), (8, 32)):
            lim = T.backend_limits(cpu, mem)
            self.assertLessEqual(int(lim["BACKEND_CPUS"]), cpu)
            self.assertLess(int(lim["BACKEND_MEM_LIMIT"].rstrip("g")), mem)


class Firewall(unittest.TestCase):
    def test_vm_interface_and_allow_lists(self):
        text = firewall.render("vault", IPS, "10.77.0.1")
        # fail closed: only internal interfaces are trusted, never "everything but the NIC"
        self.assertNotIn("iifname !=", text)
        self.assertIn('iifname { "lo", "docker0" } accept', text)
        self.assertIn('iifname "br-*" accept', text)
        self.assertIn("tcp dport 22 ip saddr { 10.77.0.1 } accept", text)
        self.assertIn("tcp dport 8200 ip saddr { 10.77.0.1, 10.77.0.14 } accept", text)
        self.assertNotIn("orcastra_nat", text)
        self.assertTrue(text.splitlines()[1].startswith("table inet orcastra_fw"))
        self.assertIn("delete table inet orcastra_fw", text)
        self.assertNotIn("flush ruleset", text)

    def test_docker_ports_matched_by_original_destination(self):
        text = firewall.render("opensearch", IPS, "10.77.0.1")
        self.assertIn("ct status dnat ct original proto-dst 9200 ip saddr { 10.77.0.1, 10.77.0.11, 10.77.0.14 }", text)
        self.assertIn("ct original proto-dst 5601 accept", text)

    def test_hairpin_only_for_authentik_on_cmp(self):
        text = firewall.render("cmp", IPS, "10.77.0.1",
                               {"listen": "192.0.2.10", "port": 9000, "target": IPS["authentik"]})
        self.assertIn("ip daddr 192.0.2.10 tcp dport 9000 dnat to 10.77.0.12:9000", text)
        self.assertEqual(text.count("dnat to"), 2)  # prerouting + output

    def test_nft_syntax_if_available(self):
        nft = "/usr/sbin/nft"
        if not os.path.exists(nft) or os.geteuid() != 0:
            self.skipTest("nft not available")
        text = firewall.render("cmp", IPS, "10.77.0.1",
                               {"listen": "192.0.2.10", "port": 9000, "target": IPS["authentik"]})
        with tempfile.NamedTemporaryFile("w", suffix=".nft", delete=False) as fh:
            fh.write(text)
        try:
            cp = subprocess.run([nft, "-c", "-f", fh.name], capture_output=True, text=True)
            self.assertEqual(cp.returncode, 0, cp.stderr)
        finally:
            os.unlink(fh.name)


class Rendering(unittest.TestCase):
    def test_asset_parity_tool(self):
        self.assertEqual(check_full_assets.main(), 0)

    def test_cmp_env(self):
        env = cmp_render.env(check_full_assets._Ctx())
        lines = [l for l in env.splitlines() if l and not l.startswith("#")]
        self.assertFalse([l for l in lines if "<" in l])
        kv = dict(l.split("=", 1) for l in lines)
        self.assertEqual(kv["POSTGRES_PORT"], "127.0.0.1:5432")
        self.assertEqual(kv["REDIS_PORT"], "127.0.0.1:6381")
        self.assertTrue(kv["AUTHENTIK_ISSUER"].endswith("/application/o/orcastra-dashboard/"))
        self.assertEqual(kv["AUTHENTIK_API_URL"], "http://10.77.0.12:9000")
        self.assertNotIn("10.0.0.0/8", kv["TRUSTED_PROXY_CIDRS"])

    def test_opensearch_without_demo_certs(self):
        y = os_render.opensearch_yml()
        self.assertNotIn("allow_unsafe_democertificates: true", y)
        self.assertNotIn("kirk", y)
        self.assertIn("nodes_dn", y)
        c = os_render.compose(3)
        self.assertIn("-Xms3g -Xmx3g", c)
        self.assertIn("DISABLE_INSTALL_DEMO_CONFIG=true", c)
        self.assertNotIn('"9300:9300"', c)
        d = os_render.dashboards_yml()
        self.assertIn("verificationMode: full", d)
        self.assertIn("cookie.secure: false", d)
        self.assertNotIn("cookie.secure: true", d)
        self.assertNotIn("opensearch.password", d.replace("# opensearch.password", ""))
        env = os_render.env(check_full_assets._Secrets(), "10.77.0.13", "192.0.2.10")
        self.assertIn("VM3_PRIVATE_IP=10.77.0.13\n", env)
        self.assertIn("FLUENTBIT_PASSWORD=", env)

    def test_guide_objects_in_order(self):
        paths = [p for p, _ in _blocks.OS_API]
        self.assertEqual(len(paths), 13)
        self.assertEqual(_blocks.OS_API[-1][1]["persistent"],
                         {"plugins.index_state_management.history.number_of_replicas": 0})
        self.assertLess(paths.index("_index_template/security-auditlog-template"),
                        paths.index("_plugins/_ism/policies/security-auditlog-policy"))
        self.assertLess(paths.index("_snapshot/orcastra-archive"),
                        paths.index("_plugins/_ism/policies/orcastra-access-policy"))
        self.assertFalse([p for p in paths if p.startswith("_plugins/_security")])

    def test_pki_matches_the_guide_dns(self):
        if not shutil.which("openssl"):
            self.skipTest("openssl missing")
        with tempfile.TemporaryDirectory() as d:
            p = pki.ensure(d, "10.77.0.13", ["192.0.2.10"])
            def subject(cert):
                return subprocess.run(["openssl", "x509", "-noout", "-subject", "-nameopt", "RFC2253",
                                       "-in", cert], stdout=subprocess.PIPE,
                                      universal_newlines=True).stdout.strip().split("=", 1)[1]
            y = os_render.opensearch_yml()
            self.assertIn(f'"{subject(p["node"])}"', y)
            self.assertIn(f'"{subject(p["admin"])}"', y)
            san = subprocess.run(["openssl", "x509", "-noout", "-ext", "subjectAltName", "-in", p["node"]],
                                 stdout=subprocess.PIPE, universal_newlines=True).stdout
            for want in ("DNS:opensearch", "IP Address:10.77.0.13", "IP Address:192.0.2.10"):
                self.assertIn(want, san)
            self.assertEqual(os.stat(p["ca_key"]).st_mode & 0o777, 0o600)
            mtime = os.stat(p["node"]).st_mtime
            pki.ensure(d, "10.77.0.13", ["192.0.2.10"])
            self.assertEqual(os.stat(p["node"]).st_mtime, mtime)

    def test_watchdog_replica_heal_is_best_effort(self):
        from orcastra_full_install import maintain, opensearch_api
        from orcastra_full_install.httpapi import HttpError
        logged = []
        ctx = type("C", (), {"log": type("L", (), {"ok": lambda s, m: logged.append(m),
                                                   "warn": lambda s, m: logged.append("W " + m)})()})()
        calls = []
        orig = (opensearch_api.health, opensearch_api.zero_replicas)
        try:
            opensearch_api.health = lambda c: {"status": "green", "unassigned_shards": 0}
            opensearch_api.zero_replicas = lambda c: calls.append(1) or []
            maintain._zero_replicas(ctx)
            self.assertEqual(calls, [])
            opensearch_api.health = lambda c: {"status": "yellow", "unassigned_shards": 1}
            opensearch_api.zero_replicas = lambda c: [".opendistro-ism-managed-index-history-x"]
            maintain._zero_replicas(ctx)
            self.assertIn("Set 0 replicas on .opendistro-ism-managed-index-history-x", logged)
            def down(c):
                raise HttpError(0, "refused", "https://x")
            opensearch_api.health = down
            maintain._zero_replicas(ctx)
            self.assertTrue(logged[-1].startswith("W OpenSearch replica check skipped"))
        finally:
            opensearch_api.health, opensearch_api.zero_replicas = orig

    def test_logging_ca_paths(self):
        from orcastra_full_install.phases import p12_vault_logging, p13_cmp
        c = p12_vault_logging.conf(check_full_assets._Ctx())
        self.assertIn("tls.verify        On", c)
        self.assertIn(p12_vault_logging.CA_PATH, c)
        self.assertNotIn("<", c)
        override = bundled.text("cmp/docker-compose.orcastra.yml")
        self.assertIn(f"./{p13_cmp.CA}:/fluent-bit/etc/orcastra-logging-ca.pem:ro", override)

    def test_cloud_init_carries_no_secret(self):
        ud = cloudinit.user_data("ssh-ed25519 AAAA test", vm=True)
        self.assertTrue(ud.startswith("#cloud-config\n"))
        doc = json.loads(ud.split("\n", 1)[1])
        self.assertEqual(doc["users"][0]["ssh_authorized_keys"], ["ssh-ed25519 AAAA test"])
        self.assertFalse(doc["ssh_pwauth"])
        self.assertIn("vm.max_map_count", ud)
        self.assertNotIn("vm.max_map_count", cloudinit.user_data("k", vm=False))

    def test_authentik_shell_code_compiles(self):
        code = authentik_api._SHELL.format(set_password="True", password=json.dumps("p'w\"x"),
                                           email=json.dumps("a@b.co"), ident=json.dumps("t"),
                                           key=json.dumps("k"))
        compile(code, "<ak shell>", "exec")


class Secrets(unittest.TestCase):
    def test_formats(self):
        key = secrets.fernet_key()
        self.assertEqual(len(key), 44)
        self.assertEqual(len(base64.urlsafe_b64decode(key)), 32)
        self.assertRegex(secrets.GENERATORS["cmp_postgres_password"](), r"^[0-9a-f]{32}$")
        self.assertRegex(secrets.alnum(40), r"^[A-Za-z0-9]{40}$")

    def test_store_persists_before_use_and_is_private(self):
        class L:
            def __init__(self):
                self.s = set()

            def add_secret(self, v):
                self.s.add(v)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "dep", "secrets.json")
            lg = L()
            st = secrets.SecretStore(p, lg)
            st.ensure_all()
            st.put("vault_dashboard_token", "hvs.example")
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
            again = secrets.SecretStore(p, L()).load()
            self.assertEqual(again.get("vault_dashboard_token"), "hvs.example")
            self.assertEqual(again.get("oidc_client_id"), st.get("oidc_client_id"))
            self.assertIn("hvs.example", lg.s)


class RemoteGuard(unittest.TestCase):
    def test_secret_in_script_refused(self):
        class R:
            _secrets = {"supersecretvalue"}

        class Lg:
            redactor = R()

        class Lx:
            log = Lg()
            lxc = "lxc"
            project = "p"
        with self.assertRaises(InstallError):
            Remote(Lx(), "i").run("echo supersecretvalue")


class SecurityHardening(unittest.TestCase):
    def test_unseal_never_sends_more_than_the_threshold(self):
        from orcastra_full_install import vault_api
        keys = vault_api.unseal_keys({"keys_base64": ["a", "b", "c", "d", "e"], "keys_threshold": 3})
        self.assertEqual(keys, ["a", "b", "c"])

    def test_http_client_ignores_proxy_env_and_verifies_tls(self):
        import urllib.request
        from orcastra_full_install.httpapi import Client
        import http.server
        import threading

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        os.environ["http_proxy"] = "http://127.0.0.1:9"  # a dead proxy: using it would fail
        os.environ["no_proxy"] = ""
        try:
            body = Client(f"http://127.0.0.1:{srv.server_port}").get("/")
            self.assertEqual(body, {"ok": True})
        finally:
            del os.environ["http_proxy"]
            del os.environ["no_proxy"]
            srv.shutdown()
        tls = Client("https://10.0.0.1:9200")
        import ssl
        self.assertEqual(tls.ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_static_validators(self):
        self.assertIsNone(C.v_image("ubuntu:24.04"))
        self.assertIsNotNone(C.v_image("--project=x"))
        self.assertIsNone(C.v_source("/dev/sdb"))
        self.assertIsNotNone(C.v_source("dev/sdb; rm -rf /"))

    def test_remote_write_uses_mktemp_and_no_symlink_chown(self):
        seen = {}

        class P:
            def run(self, argv, **kw):
                seen["script"] = argv[-1]
                from orcastra_core.proc import Result
                return Result(0, "", "", argv)

        class R:
            _secrets = set()

        class Lg:
            redactor = R()

        class Lx:
            log = Lg()
            lxc = "lxc"
            project = "p"
            proc = P()
        Remote(Lx(), "i").write("/etc/x.conf", "body", mode="0600", owner="1000:1000")
        self.assertIn("mktemp -p", seen["script"])
        self.assertIn("chown -h 1000:1000", seen["script"])


class HostFixRevert(unittest.TestCase):
    def test_revert_uses_the_right_ufw_syntax_and_removes_sysctl(self):
        from orcastra_full_install import hostfix

        calls = []

        class P:
            def run(self, argv, **kw):
                calls.append(argv)

        class St:
            def artifact(self, k):
                return {"bridge": "br9", "fixes": ["ufw", "sysctl"]}

        class Ctx:
            proc = P()
            state = St()
        with tempfile.TemporaryDirectory() as d:
            old = hostfix.SYSCTL_PATH
            hostfix.SYSCTL_PATH = os.path.join(d, "60-orcastra.conf")
            open(hostfix.SYSCTL_PATH, "w").close()
            try:
                hostfix.revert(Ctx())
                self.assertFalse(os.path.exists(hostfix.SYSCTL_PATH))
            finally:
                hostfix.SYSCTL_PATH = old
        self.assertIn(["ufw", "delete", "allow", "in", "on", "br9"], calls)
        self.assertIn(["ufw", "route", "delete", "allow", "in", "on", "br9"], calls)
        self.assertIn(["ufw", "route", "delete", "allow", "out", "on", "br9"], calls)


class WizardHelpers(unittest.TestCase):
    def test_ports_and_owners(self):
        self.assertEqual(W.expand_ports("80,443,8000-8002"), {80, 443, 8000, 8001, 8002})
        fwds = [{"network": "lxdbr0", "listen_address": "1.2.3.4", "config": {},
                 "ports": [{"protocol": "tcp", "listen_port": "9000", "target_address": "10.0.0.9"}]}]
        self.assertIn("10.0.0.9", W.forward_port_owner(fwds, "1.2.3.4", 9000))
        self.assertIsNone(W.forward_port_owner(fwds, "1.2.3.4", 9001))
        self.assertEqual(W.listen_on_other_network(fwds, "1.2.3.4", "orcabr0"), "lxdbr0")
        self.assertIsNone(W.listen_on_other_network(fwds, "1.2.3.4", "lxdbr0"))

    def test_leases(self):
        owners = W.lease_owners([{"address": "10.77.0.11", "hostname": "orca-vault", "project": "orcastra"},
                                 {"address": "10.77.0.20", "hostname": "web", "project": "default"},
                                 {"address": "fd42::1", "hostname": "x"}])
        self.assertIsNone(W.ip_taken(owners, "10.77.0.11", "orcastra"))  # our own, re-run
        self.assertEqual(W.ip_taken(owners, "10.77.0.20", "orcastra"), "default/web")
        self.assertEqual(W.default_ip("10.77.0.1/24", "cmp"), "10.77.0.14")


if __name__ == "__main__":
    unittest.main()
