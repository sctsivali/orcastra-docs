"""Docker Hub lookups for the CMP release images (anonymous, the repository is public).
Best effort: a lookup that fails never blocks the install, it only loses the hint."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import List, Optional

from . import topology as T

_HUB = "https://hub.docker.com/v2/repositories/{repo}/tags?page_size=100&ordering=last_updated"
_TOKEN = "https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
_MANIFEST = "https://registry-1.docker.io/v2/{repo}/manifests/{tag}"
_RELEASE = re.compile(r"^backend-(\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+(?:-hotfix\d+)?)?)$")


def published_versions(timeout: float = 8.0) -> Optional[List[str]]:
    """Release versions that have a backend image on Docker Hub, newest first."""
    try:
        with urllib.request.urlopen(_HUB.format(repo=T.CMP_REGISTRY_REPO), timeout=timeout) as r:
            rows = json.load(r).get("results", [])
    except (OSError, ValueError):
        return None
    out = []
    for row in rows:
        m = _RELEASE.match(row.get("name", ""))
        if m and "prehotfix" not in m.group(1) and m.group(1) not in out:
            out.append(m.group(1))
    return out


def image_exists(tag: str, timeout: float = 10.0) -> Optional[bool]:
    """True/False when the registry answered, None when it could not be reached."""
    try:
        with urllib.request.urlopen(_TOKEN.format(repo=T.CMP_REGISTRY_REPO), timeout=timeout) as r:
            token = json.load(r)["token"]
        req = urllib.request.Request(
            _MANIFEST.format(repo=T.CMP_REGISTRY_REPO, tag=tag), method="HEAD",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.oci.image.index.v1+json, "
                               "application/vnd.docker.distribution.manifest.list.v2+json, "
                               "application/vnd.docker.distribution.manifest.v2+json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except urllib.error.HTTPError as exc:
        return False if exc.code == 404 else None
    except (OSError, ValueError, KeyError):
        return None
