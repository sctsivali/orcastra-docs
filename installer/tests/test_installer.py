"""Stdlib unittest suite for the Orcastra Mini installer.

Run:  cd installer && python3 -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

INSTALLER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, INSTALLER)

from orcastra_mini_install import templates as T          # noqa: E402
from orcastra_mini_install import _blocks                 # noqa: E402
from orcastra_mini_install import netutil                 # noqa: E402
from orcastra_mini_install.state import State             # noqa: E402
from orcastra_mini_install.cli import build_parser, merge_answers, VALUE_DEFAULTS  # noqa: E402
from orcastra_mini_install.phases import p05_secrets       # noqa: E402


class TemplateParity(unittest.TestCase):
    def test_compose_default_tag_is_verbatim(self):
        self.assertEqual(T.render_compose(), _blocks.COMPOSE)

    def test_compose_tag_substitution(self):
        c = T.render_compose("2.0.0")
        self.assertIn("svlct/orcastra-dashboard-mini:backend-2.0.0", c)
        self.assertIn("svlct/orcastra-dashboard-mini:frontend-2.0.0", c)
        self.assertNotIn("backend-1.0.0-RC1", c)
        # only the image tag changed; the APP_VERSION fallback default stays
        self.assertIn("${APP_VERSION:-1.0.0-RC1}", c)

    def test_nginx_and_vault_verbatim(self):
        self.assertEqual(T.nginx_conf(), _blocks.NGINX_CONF)
        self.assertEqual(T.vault_hcl(), _blocks.VAULT_HCL)


class EnvRendering(unittest.TestCase):
    def test_key_set_matches_docs(self):
        rendered = T.render_env({k: "v" for k in T.FILLABLE_ENV_KEYS})
        keys = [l.split("=", 1)[0] for l in rendered.splitlines()
                if l and not l.startswith("#") and "=" in l]
        self.assertEqual(keys, T.env_doc_keys())

    def test_fixed_lines_preserved(self):
        env = T.render_env({"HTTPS_PORT": "6969"})
        self.assertIn("AUTH_MODE=client-cert", env)
        self.assertIn("VAULT_ENABLED=true", env)
        self.assertIn("AUDIT_DB_ENABLED=true", env)

    def test_refuse_non_fillable_key(self):
        with self.assertRaises(KeyError):
            T.render_env({"AUTH_MODE": "nope"})

    def test_set_and_get_value(self):
        env = T.render_env({"VAULT_TOKEN": ""})
        env2 = T.set_env_value(env, "VAULT_TOKEN", "hvs.abc")
        self.assertEqual(T.get_env_value(env2, "VAULT_TOKEN"), "hvs.abc")
        env3 = T.set_env_value(env2, "BOOTSTRAP_ADMIN_TOKEN", "")
        self.assertEqual(T.get_env_value(env3, "BOOTSTRAP_ADMIN_TOKEN"), "")

    def test_set_missing_key_raises(self):
        with self.assertRaises(KeyError):
            T.set_env_value("FOO=1\n", "BAR", "x")


class Secrets(unittest.TestCase):
    def test_generators_distinct_and_nonempty(self):
        vals = {name: gen() for name, gen in p05_secrets._GENERATORS.items()}
        for name, v in vals.items():
            self.assertTrue(v and len(v) >= 20, name)

    def test_fernet_like_key_length(self):
        # NEXTAUTH_SECRET base64(32 bytes) == 44 chars
        self.assertEqual(len(p05_secrets._GENERATORS["NEXTAUTH_SECRET"]()), 44)


class HostClassification(unittest.TestCase):
    def test_ip_vs_dns(self):
        self.assertTrue(netutil.is_ip_literal("10.0.0.5"))
        self.assertTrue(netutil.is_ip_literal("::1"))
        self.assertFalse(netutil.is_ip_literal("host.example.com"))
        self.assertFalse(netutil.is_ip_literal("localhost"))


class StateLedger(unittest.TestCase):
    def test_phase_and_fingerprint_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "install-state.json")
            st = State.load(path)
            self.assertEqual(st.phase_status("preflight"), "pending")
            st.set_phase("preflight", "done")
            st.record_secret("SECRET_KEY", "abcd1234")
            st2 = State.load(path)
            self.assertTrue(st2.is_done("preflight"))
            self.assertEqual(st2.data["secret_fingerprints"]["SECRET_KEY"],
                             State.fingerprint("abcd1234"))
            with open(path, encoding="utf-8") as fh:
                self.assertNotIn("abcd1234", fh.read())  # value never persisted


class AnswerFileMerge(unittest.TestCase):
    def _flags(self, argv):
        return build_parser().parse_args(argv)

    def test_defaults_applied(self):
        f = self._flags([])
        merge_answers(f, {})
        for dest, default in VALUE_DEFAULTS.items():
            self.assertEqual(getattr(f, dest), default)

    def test_cli_overrides_answers(self):
        f = self._flags(["--https-port", "7000"])
        merge_answers(f, {"HTTPS_PORT": "8000"})
        self.assertEqual(f.https_port, 7000)

    def test_answers_override_default(self):
        f = self._flags([])
        merge_answers(f, {"HTTPS_PORT": "8443", "CONTAINER_PREFIX": "acme"})
        self.assertEqual(f.https_port, 8443)
        self.assertEqual(f.container_prefix, "acme")

    def test_answer_bool(self):
        f = self._flags([])
        merge_answers(f, {"CONVENIENCE": "true"})
        self.assertTrue(f.convenience)


if __name__ == "__main__":
    unittest.main()
