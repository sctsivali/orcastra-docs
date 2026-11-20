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
from orcastra_core import netutil                         # noqa: E402
from orcastra_core.state import State                     # noqa: E402
from orcastra_mini_install.cli import build_parser, merge_answers, VALUE_DEFAULTS  # noqa: E402
from orcastra_mini_install.phases import p05_secrets       # noqa: E402


class TemplateParity(unittest.TestCase):
    def test_compose_default_tag_is_verbatim(self):
        self.assertEqual(T.render_compose(), _blocks.COMPOSE)

    def test_compose_tag_substitution(self):
        c = T.render_compose("2.0.0")
        # The tag follows APP_VERSION in .env; the argument only moves the image default.
        self.assertIn("svlct/orcastra-dashboard-mini}:backend-${APP_VERSION:-2.0.0}", c)
        self.assertIn("svlct/orcastra-dashboard-mini}:frontend-${APP_VERSION:-2.0.0}", c)
        # the APP_VERSION environment default handed to the backend is not an image line
        self.assertIn("- APP_VERSION=${APP_VERSION:-1.0.0}", c)

    def test_explicit_images_replace_the_image_lines(self):
        c = T.render_compose("1.0.0", "registry.example/mini:be-x", "registry.example/mini:fe-x")
        self.assertIn("    image: registry.example/mini:be-x", c)
        self.assertIn("    image: registry.example/mini:fe-x", c)
        self.assertNotIn("orcastra-dashboard-mini}:backend-", c)

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
        self.assertIn("VAULT_ENABLED=true", env)
        self.assertIn("VAULT_PKI_ROLE=lxd", env)
        self.assertIn("AUDIT_DB_ENABLED=true", env)

    def test_refuse_non_fillable_key(self):
        with self.assertRaises(KeyError):
            T.render_env({"VAULT_ENABLED": "false"})

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


# ---------------------------------------------------------------------------
# Upgrade (pure parts; the live upgrade runs against a real RC1 install in QA)
# ---------------------------------------------------------------------------

from orcastra_mini_install import upgrade as U       # noqa: E402
from orcastra_mini_install import backups as B       # noqa: E402

RC1_ENV = """# Profile
CONTAINER_PREFIX=orcastra-mini
APP_VERSION=1.0.0-RC1
HTTPS_PORT=6969
AUTH_MODE=client-cert
NEXT_PUBLIC_AUTH_MODE=client-cert
POSTGRES_PASSWORD=keepme
SECRET_KEY=%s
LOCAL_JWT_SECRET=%s
AUTH_PROXY_SECRET=short
BOOTSTRAP_ADMIN_TOKEN=
VAULT_ENABLED=true
VAULT_TOKEN=hvs.keep
LOCAL_JWT_TTL_SECONDS=3600
CUSTOM_OPERATOR_KEY=stays
""" % ("a" * 64, "b" * 64)


class UpgradeEnvMerge(unittest.TestCase):
    def test_existing_values_stay_and_the_version_moves(self):
        merged, added, rotated = U.merge_env(RC1_ENV, "1.0.0")
        self.assertIn("APP_VERSION=1.0.0\n", merged)
        for line in ("POSTGRES_PASSWORD=keepme", "VAULT_TOKEN=hvs.keep", "LOCAL_JWT_TTL_SECONDS=3600",
                     "CUSTOM_OPERATOR_KEY=stays", "LOCAL_JWT_SECRET=" + "b" * 64):
            self.assertIn(line, merged)

    def test_new_documented_keys_are_added_with_their_defaults(self):
        merged, added, _ = U.merge_env(RC1_ENV, "1.0.0")
        for key in ("HTTPS_BIND", "UPLOADS_DIR", "VAULT_PKI_ROLE", "AUDIT_DB_RETENTION_DAYS", "CERT_SELF_RENEW_ENABLED"):
            self.assertIn(key, added)
        self.assertIn("HTTPS_BIND=0.0.0.0", merged)
        # a placeholder value is never written into a live .env
        self.assertNotIn("<set a strong value>", merged)

    def test_a_secret_too_short_for_this_release_is_replaced(self):
        merged, _, rotated = U.merge_env(RC1_ENV, "1.0.0")
        self.assertEqual(rotated, ["AUTH_PROXY_SECRET"])
        value = T.get_env_value(merged, "AUTH_PROXY_SECRET")
        self.assertGreaterEqual(len(value), 32)

    def test_merging_twice_adds_nothing_more(self):
        once, _, _ = U.merge_env(RC1_ENV, "1.0.0")
        twice, added, rotated = U.merge_env(once, "1.0.0")
        self.assertEqual((added, rotated), ([], []))
        self.assertEqual(once, twice)


class UpgradeEmailPlan(unittest.TestCase):
    def test_rules_match_the_database_revision(self):
        rows = [("aa", "admin", ""), ("bb", "qa-partner.users.orcastra.internal", "QA Partner"),
                ("cc", "t1.users.orcastra.internal", "same@example.org"),
                ("dd", "t2.users.orcastra.internal", "same@example.org"),
                ("ee", "solo.users.orcastra.internal", "Solo@Example.org")]
        plan = {fp: (email, source) for fp, _cn, _d, email, source in U.plan_emails(rows, {})}
        self.assertEqual(plan["aa"], ("admin@users.orcastra.internal", "generated"))
        self.assertEqual(plan["bb"], ("qa-partner@users.orcastra.internal", "generated"))
        self.assertEqual(plan["cc"][1], "generated")   # a shared display name goes to neither
        self.assertEqual(plan["dd"][1], "generated")
        self.assertEqual(plan["ee"], ("solo@example.org", "display name"))

    def test_a_chosen_address_wins_and_none_repeat(self):
        rows = [("aa", "x.y", "Ops"), ("bb", "x.z", "Other")]
        plan = U.plan_emails(rows, {"aa": "ops@example.org"})
        self.assertEqual(plan[0][3], "ops@example.org")
        self.assertEqual(len({p[3] for p in plan}), 2)


class UpgradeOrdering(unittest.TestCase):
    def test_a_release_candidate_sorts_before_its_release(self):
        self.assertLess(U._version_tuple("1.0.0-RC1"), U._version_tuple("1.0.0"))
        self.assertLess(U._version_tuple("1.0.0"), U._version_tuple("1.0.1"))
        self.assertLess(U._version_tuple("1.0.0-RC1"), U._version_tuple("1.0.0-RC2"))


class BackupRestoreSafety(unittest.TestCase):
    def test_a_backup_cannot_write_outside_the_install_dir(self):
        import tarfile
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as root:
            src = os.path.join(root, "b")
            os.makedirs(src)
            evil = os.path.join(root, "evil.txt")
            with open(evil, "w") as fh:
                fh.write("x")
            with tarfile.open(os.path.join(src, "files.tar.gz"), "w:gz") as tar:
                tar.add(evil, arcname="../../escape.txt")
            ctx = SimpleNamespace(install_dir=os.path.join(root, "install"))
            os.makedirs(ctx.install_dir)
            with self.assertRaises(Exception):
                B.restore_files(ctx, src)
            self.assertFalse(os.path.exists(os.path.join(root, "escape.txt")))


class BackupLinkSafety(unittest.TestCase):
    def _archive(self, root, build):
        import tarfile
        src = os.path.join(root, "b")
        os.makedirs(src)
        with tarfile.open(os.path.join(src, "files.tar.gz"), "w:gz") as tar:
            build(tar)
        return src

    def test_a_link_in_a_backup_is_refused(self):
        import tarfile
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as root:
            def build(tar):
                info = tarfile.TarInfo("config/backend/x")
                info.type, info.linkname = tarfile.SYMTYPE, "/root"
                tar.addfile(info)
            src = self._archive(root, build)
            ctx = SimpleNamespace(install_dir=os.path.join(root, "install"))
            os.makedirs(ctx.install_dir)
            with self.assertRaises(Exception):
                B.restore_files(ctx, src)

    def test_a_directory_swapped_for_a_link_after_the_backup_is_refused(self):
        import io
        import tarfile
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as root:
            outside = os.path.join(root, "outside")
            os.makedirs(outside)
            def build(tar):
                data = b"payload"
                info = tarfile.TarInfo("config/p/.bashrc")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            src = self._archive(root, build)
            ctx = SimpleNamespace(install_dir=os.path.join(root, "install"))
            os.makedirs(os.path.join(ctx.install_dir, "config"))
            os.symlink(outside, os.path.join(ctx.install_dir, "config", "p"))
            with self.assertRaises(Exception):
                B.restore_files(ctx, src)
            self.assertFalse(os.path.exists(os.path.join(outside, ".bashrc")))

    def test_the_archive_leaves_links_out(self):
        import tarfile
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as root:
            install = os.path.join(root, "install")
            os.makedirs(os.path.join(install, "config", "backend"))
            with open(os.path.join(install, "config", "backend", "node_metadata.json"), "w") as fh:
                fh.write("{}")
            os.symlink("/etc", os.path.join(install, "config", "backend", "planted"))
            dest = os.path.join(root, "b")
            os.makedirs(dest)
            B.archive_files(SimpleNamespace(install_dir=install), dest)
            with tarfile.open(os.path.join(dest, "files.tar.gz")) as tar:
                names = tar.getnames()
            self.assertIn("config/backend/node_metadata.json", names)
            self.assertNotIn("config/backend/planted", names)
