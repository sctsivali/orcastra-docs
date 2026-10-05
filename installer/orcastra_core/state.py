"""Persistent install state (JSON, mode 0600).

Drives idempotent re-runs: a phase ledger records what is `done`, an artifacts map records
what exists, and SHA-256 fingerprints (never plaintext) let the installer detect that
secrets already exist without reading their values into memory or logs.

In dry-run mode nothing is written, so a later real run starts from the true state.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from typing import Any, Optional

from .errors import InstallError

SCHEMA = 1


class State:
    def __init__(self, path: str, *, dry_run: bool = False) -> None:
        self.path = path
        self.dry_run = dry_run
        self.recovered_from: Optional[str] = None
        self.data: dict = {
            "schema": SCHEMA,
            "phases": {},
            "artifacts": {},
            "config": {},
            "secret_fingerprints": {},
        }

    # -- load/save -----------------------------------------------------------
    @classmethod
    def load(cls, path: str, *, strict: bool = False, dry_run: bool = False) -> "State":
        """Load the ledger. A corrupt file raises in strict mode (the state is an inventory
        that uninstall depends on). Otherwise it is moved aside, never silently discarded,
        and a fresh ledger is used. `recovered_from` names the preserved copy."""
        st = cls(path, dry_run=dry_run)
        if not os.path.exists(path):
            return st
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if not isinstance(loaded, dict):
                raise ValueError("state root is not an object")
        except (ValueError, OSError) as exc:
            if strict:
                raise InstallError(
                    f"State file {path} is unreadable ({exc}).",
                    remediation="Restore it from the .bak copy next to it, or move it away "
                                "only if you know nothing was installed yet.")
            if not dry_run:
                aside = f"{path}.corrupt.{int(time.time())}"
                os.replace(path, aside)
                st.recovered_from = aside
            return st
        st.data.update(loaded)
        return st

    def save(self) -> None:
        if self.dry_run:
            return
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=d, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, sort_keys=True)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self.path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # -- phases --------------------------------------------------------------
    def phase_status(self, name: str) -> str:
        return self.data["phases"].get(name, "pending")

    def is_done(self, name: str) -> bool:
        return self.phase_status(name) == "done"

    def set_phase(self, name: str, status: str) -> None:
        self.data["phases"][name] = status
        self.save()

    def reset_phases(self, names: list) -> None:
        for n in names:
            self.data["phases"].pop(n, None)
        self.save()

    # -- artifacts -----------------------------------------------------------
    def artifact(self, name: str, default: Any = None) -> Any:
        return self.data["artifacts"].get(name, default)

    def set_artifact(self, name: str, value: Any) -> None:
        self.data["artifacts"][name] = value
        self.save()

    def drop_artifact(self, name: str) -> None:
        self.data["artifacts"].pop(name, None)
        self.save()

    # -- config snapshot -----------------------------------------------------
    def set_config(self, cfg: dict) -> None:
        self.data["config"] = dict(cfg)
        self.save()

    # -- secret fingerprints -------------------------------------------------
    @staticmethod
    def fingerprint(value: str) -> str:
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def record_secret(self, name: str, value: str) -> None:
        self.data["secret_fingerprints"][name] = self.fingerprint(value)
        self.save()
