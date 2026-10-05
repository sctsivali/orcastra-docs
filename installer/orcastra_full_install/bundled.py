"""Read files shipped inside the package (works from the source tree and from the .pyz)."""
from __future__ import annotations

import hashlib
import pkgutil

from orcastra_core.errors import InstallError


def text(rel: str) -> str:
    data = pkgutil.get_data("orcastra_full_install", "assets/" + rel)
    if data is None:
        raise InstallError(f"internal error: asset {rel} is missing from the installer")
    return data.decode("utf-8")


def text_sha256(rel: str, expected: str) -> str:
    body = text(rel)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if digest != expected:
        raise InstallError(f"Asset {rel} is corrupted (sha256 {digest}, expected {expected}).",
                           remediation="Download the installer again.")
    return body
