"""Checks on the release manifest and the tool that edits it. Run: python3 -m unittest discover scripts/releases"""

import copy
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "installer"))

import manifest as m  # noqa: E402


def published():
    return json.loads(m.MANIFEST.read_text(encoding="utf-8"))


class PublishedManifest(unittest.TestCase):
    def test_it_validates(self):
        m.load()

    def test_its_latest_is_the_installers_latest(self):
        from orcastra_full_install import topology

        self.assertEqual(published()["releases"][0]["version"], topology.CMP_LATEST)

    def test_every_installer_release_is_listed(self):
        from orcastra_full_install import topology

        listed = {r["version"] for r in published()["releases"]}
        self.assertLessEqual(set(topology.CMP_RELEASES), listed)

    def test_every_release_links_to_its_own_section(self):
        page = (m.ROOT / "docs" / "operations" / "releases.md").read_text(encoding="utf-8")
        for release in published()["releases"]:
            self.assertIn("{#" + m.anchor(release["version"]) + "}", page, release["version"])


class Rules(unittest.TestCase):
    def broken(self, change, message):
        data = copy.deepcopy(published())
        change(data)
        with self.assertRaisesRegex(m.Invalid, message):
            m.validate(data)

    def test_order_and_status_rules(self):
        self.broken(lambda d: d["releases"].reverse(), "newest first")
        self.broken(lambda d: d["releases"][1].update(status="latest"), "exactly one")
        self.broken(lambda d: d["releases"][-1].update(status="supported"), "better state")
        self.broken(lambda d: d.update(schema_version=2), "schema_version")

    def test_links_stay_on_the_documentation(self):
        self.broken(lambda d: d["releases"][0].update(notes_url="https://example.com/x"), "documentation")
        self.broken(lambda d: d["releases"][0].update(upgrade_url="https://github.com/other/repo"), "orcastra-docs")

    def test_values(self):
        self.broken(lambda d: d["releases"][0].update(version="1.0.0-rc9"), "not an Orcastra version")
        self.broken(lambda d: d["releases"][0].update(released="soon"), "date")
        self.broken(lambda d: d["releases"][0].update(digests={"backend": "sha256:nope"}), "digest")


class Editing(unittest.TestCase):
    def test_adding_a_release_makes_it_latest_and_keeps_the_previous_supported(self):
        data = m.add(copy.deepcopy(published()), "1.0.0-RC5", "2026-11-02")
        self.assertEqual([r["status"] for r in data["releases"][:2]], ["latest", "supported"])
        self.assertEqual(data["releases"][0]["images"]["backend"], "svlct/orcastra-dashboard:backend-1.0.0-RC5")
        self.assertTrue(data["releases"][0]["notes_url"].endswith("#1-0-0-rc5"))

    def test_a_release_cannot_be_added_twice(self):
        with self.assertRaisesRegex(m.Invalid, "already listed"):
            m.add(copy.deepcopy(published()), "1.0.0-RC4-hotfix3", "2026-11-02")

    def test_a_status_change_that_breaks_the_order_is_refused(self):
        with self.assertRaisesRegex(m.Invalid, "better state"):
            m.set_status(copy.deepcopy(published()), "1.0.0-RC4", "supported")


class Images(unittest.TestCase):
    def test_a_changed_digest_on_docker_hub_is_reported(self):
        data = copy.deepcopy(published())
        data["releases"] = data["releases"][:1]
        data["releases"][0]["digests"] = {"backend": "sha256:" + "a" * 64, "frontend": "sha256:" + "b" * 64}

        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(url, timeout):
            return Answer(json.dumps({"digest": "sha256:" + "c" * 64}).encode())

        problems = m.check_images(data, opener=opener)
        self.assertEqual(len(problems), 2)
        self.assertIn("Docker Hub now serves", problems[0])


if __name__ == "__main__":
    unittest.main()
