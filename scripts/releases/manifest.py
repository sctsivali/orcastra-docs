#!/usr/bin/env python3
"""The Orcastra CMP release manifest: docs/releases/manifest.json.

Running dashboards read this file to tell their Administrators about newer, deprecated or
end-of-life versions. It is published as is, unsigned, from this public repository, so every
change goes through this tool and its checks, which mirror what the dashboard accepts
(orcastra-cmp backend/app/services/release_check/manifest.py).

    python3 scripts/releases/manifest.py validate
    python3 scripts/releases/manifest.py add 1.0.0-RC5 --released 2026-11-02 [--security]
    python3 scripts/releases/manifest.py set-status 1.0.0-RC4-hotfix2 end_of_life
    python3 scripts/releases/manifest.py check-images [--write]

Standard library only, Python 3.8 or later.
"""

import argparse
import json
import re
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs" / "releases" / "manifest.json"
IMAGE = "svlct/orcastra-dashboard"
DOCS = "https://docs.orcastra.io/en/latest/operations"
MAX_BYTES = 64 * 1024
MAX_RELEASES = 200
STATUSES = ("latest", "supported", "deprecated", "end_of_life")
LINK_HOSTS = {"docs.orcastra.io", "orcastra.readthedocs.io", "github.com"}
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-RC(\d+))?(?:-hotfix(\d+))?$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class Invalid(ValueError):
    pass


def key(version):
    match = VERSION.match(version)
    if not match:
        raise Invalid("{!r} is not an Orcastra version".format(version))
    major, minor, patch, rc, hotfix = match.groups()
    final, rc_number = (1, 0) if rc is None else (0, int(rc))
    return (int(major), int(minor), int(patch), final, rc_number, int(hotfix or 0))


def anchor(version):
    return version.lower().replace(".", "-")


def links(version):
    return {
        "notes_url": "{}/releases/#{}".format(DOCS, anchor(version)),
        "upgrade_url": "{}/upgrading/".format(DOCS),
    }


def _check_link(value, where):
    if value is None:
        return
    parts = urlsplit(value) if isinstance(value, str) else None
    if not parts or parts.scheme != "https" or (parts.hostname or "") not in LINK_HOSTS or parts.port or parts.username:
        raise Invalid("{} must be an https link to the Orcastra documentation".format(where))
    if parts.hostname == "github.com" and not parts.path.startswith("/sctsivali/orcastra-docs"):
        raise Invalid("{} must point into the orcastra-docs repository".format(where))


def validate(data, raw_size=0):
    """Raise Invalid on the first rule the manifest breaks. Returns the releases."""
    if raw_size > MAX_BYTES:
        raise Invalid("the manifest is larger than 64 KiB")
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise Invalid("schema_version must be 1")
    releases = data.get("releases")
    if not isinstance(releases, list) or not releases or len(releases) > MAX_RELEASES:
        raise Invalid("releases must be a list of 1 to {} entries".format(MAX_RELEASES))
    keys = []
    for index, release in enumerate(releases):
        where = "releases[{}]".format(index)
        if not isinstance(release, dict):
            raise Invalid("{} must be an object".format(where))
        keys.append(key(release.get("version", "")))
        if release.get("status") not in STATUSES:
            raise Invalid("{}.status must be one of {}".format(where, ", ".join(STATUSES)))
        if release.get("released") is not None:
            try:
                date.fromisoformat(release["released"])
            except (TypeError, ValueError):
                raise Invalid("{}.released must be a date (YYYY-MM-DD)".format(where))
        if not isinstance(release.get("security", False), bool):
            raise Invalid("{}.security must be true or false".format(where))
        _check_link(release.get("notes_url"), where + ".notes_url")
        _check_link(release.get("upgrade_url"), where + ".upgrade_url")
        for part, digest in (release.get("digests") or {}).items():
            if part not in ("backend", "frontend") or not DIGEST.match(str(digest)):
                raise Invalid("{}.digests.{} must be a sha256 digest".format(where, part))
    if any(a <= b for a, b in zip(keys, keys[1:])):
        raise Invalid("releases must be listed newest first, each version once")
    statuses = [r["status"] for r in releases]
    if statuses[0] != "latest" or "latest" in statuses[1:]:
        raise Invalid("exactly one release, the first, is latest")
    ranks = [STATUSES.index(s) for s in statuses]
    if any(a > b for a, b in zip(ranks, ranks[1:])):
        raise Invalid("an older release cannot be in a better state than a newer one")
    return releases


def load():
    raw = MANIFEST.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    validate(data, len(raw))
    return data


def save(data):
    data["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    validate(json.loads(text), len(text.encode()))
    MANIFEST.write_text(text, encoding="utf-8")


def add(data, version, released, security=False):
    key(version)
    if any(r["version"] == version for r in data["releases"]):
        raise Invalid("{} is already listed".format(version))
    for release in data["releases"]:
        if release["status"] == "latest":
            release["status"] = "supported"
    entry = {"version": version, "status": "latest", "released": released, "security": security}
    entry.update(links(version))
    entry["images"] = {part: "{}:{}-{}".format(IMAGE, part, version) for part in ("backend", "frontend")}
    data["releases"].insert(0, entry)
    data["releases"].sort(key=lambda r: key(r["version"]), reverse=True)
    validate(data)
    return data


def set_status(data, version, status):
    match = [r for r in data["releases"] if r["version"] == version]
    if not match:
        raise Invalid("{} is not listed".format(version))
    match[0]["status"] = status
    validate(data)
    return data


def hub_digest(tag, opener=urllib.request.urlopen):
    url = "https://hub.docker.com/v2/repositories/{}/tags/{}".format(IMAGE, tag)
    with opener(url, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))
    digest = body.get("digest")
    if not digest or not DIGEST.match(digest):
        raise Invalid("Docker Hub has no digest for {}:{}".format(IMAGE, tag))
    return digest


def check_images(data, write=False, opener=urllib.request.urlopen):
    """Every listed release's images exist on Docker Hub; with write, record their digests."""
    problems = []
    for release in data["releases"]:
        digests = {}
        for part in ("backend", "frontend"):
            tag = "{}-{}".format(part, release["version"])
            try:
                digests[part] = hub_digest(tag, opener)
            except Exception as exc:  # noqa: BLE001 - reported per tag
                problems.append("{}: {}".format(tag, exc))
        known = release.get("digests") or {}
        for part, digest in digests.items():
            if known.get(part) and known[part] != digest:
                problems.append("{}-{}: Docker Hub now serves {}, the manifest records {}".format(
                    part, release["version"], digest, known[part]))
        if write and len(digests) == 2:
            release["digests"] = digests
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    adding = sub.add_parser("add")
    adding.add_argument("version")
    adding.add_argument("--released", default=date.today().isoformat())
    adding.add_argument("--security", action="store_true", help="the release fixes a security issue")
    status = sub.add_parser("set-status")
    status.add_argument("version")
    status.add_argument("status", choices=STATUSES[1:])
    images = sub.add_parser("check-images")
    images.add_argument("--write", action="store_true", help="record the digests Docker Hub serves")
    args = parser.parse_args(argv)
    try:
        data = load()
        if args.command == "validate":
            print("manifest: {} releases, latest {}".format(len(data["releases"]), data["releases"][0]["version"]))
            return 0
        if args.command == "add":
            save(add(data, args.version, args.released, args.security))
        elif args.command == "set-status":
            save(set_status(data, args.version, args.status))
        elif args.command == "check-images":
            problems = check_images(data, write=args.write)
            if problems:
                print("\n".join(problems), file=sys.stderr)
                return 1
            if args.write:
                save(data)
            print("images: every listed release is on Docker Hub")
            return 0
        print("manifest: written, latest {}".format(load()["releases"][0]["version"]))
        return 0
    except (Invalid, ValueError, OSError) as exc:
        print("manifest: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
