"""Persistent install state (`install-state.json`, mode 0600).

Drives idempotent re-runs: a phase ledger records what is `done`, an artifacts map records
what exists, and SHA-256 fingerprints (never plaintext) let the installer detect that
secrets already exist without reading their values into memory or logs.
"""
import hashlib
import json
import os
import tempfile

SCHEMA = 1


class State:
    def __init__(self, path: str):
        self.path = path
        self.data = {
            "schema": SCHEMA,
            "phases": {},
            "artifacts": {},
            "config": {},
            "secret_fingerprints": {},
        }

    # -- load/save -----------------------------------------------------------
    @classmethod
    def load(cls, path: str) -> "State":
        st = cls(path)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    st.data.update(loaded)
            except (ValueError, OSError):
                pass  # corrupt/partial state: start fresh, detection probes still guard
        return st

    def save(self):
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(self.path)), prefix=".state-", suffix=".tmp")
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

    def set_phase(self, name: str, status: str):
        self.data["phases"][name] = status
        self.save()

    # -- artifacts -----------------------------------------------------------
    def artifact(self, name: str, default=None):
        return self.data["artifacts"].get(name, default)

    def set_artifact(self, name: str, value):
        self.data["artifacts"][name] = value
        self.save()

    # -- config snapshot -----------------------------------------------------
    def set_config(self, cfg: dict):
        self.data["config"] = dict(cfg)
        self.save()

    # -- secret fingerprints -------------------------------------------------
    @staticmethod
    def fingerprint(value: str) -> str:
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def record_secret(self, name: str, value: str):
        self.data["secret_fingerprints"][name] = self.fingerprint(value)
        self.save()
