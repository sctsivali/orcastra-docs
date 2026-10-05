"""Generated credentials, kept in one JSON file on the host (0600 in a 0700 dir).

Every value is created once and reused on re-runs, so rendered files stay byte-identical
and nothing restarts needlessly. Values are registered with the log redactor as soon as
they are loaded or created.
"""
from __future__ import annotations

import base64
import json
import os
import secrets as pysecrets
import string
import tempfile
from typing import Callable, Dict, Optional

from orcastra_core.errors import InstallError

_ALNUM = string.ascii_letters + string.digits


def alnum(n: int = 32) -> str:
    """Letters and digits only: safe unquoted in .env, YAML, JSON and Fluent Bit configs."""
    return "".join(pysecrets.choice(_ALNUM) for _ in range(n))


def fernet_key() -> str:
    """A Fernet key: urlsafe base64 of 32 random bytes, 44 characters with padding."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


GENERATORS: Dict[str, Callable[[], str]] = {
    "authentik_secret_key": lambda: alnum(64),
    "authentik_pg_pass": lambda: alnum(40),
    "authentik_bootstrap_token": lambda: alnum(60),
    "admin_password": lambda: alnum(24),
    "oidc_client_id": lambda: alnum(40),
    "oidc_client_secret": lambda: alnum(64),
    "os_admin_password": lambda: alnum(32),
    "os_dashboards_password": lambda: alnum(32),
    "os_audit_viewer_password": lambda: alnum(32),
    "fluentbit_password": lambda: alnum(32),
    "cmp_postgres_password": lambda: pysecrets.token_hex(16),
    "cmp_secret_key": lambda: pysecrets.token_hex(32),
    "cmp_nextauth_secret": lambda: base64.b64encode(os.urandom(32)).decode(),
    "cmp_redis_encryption_key": fernet_key,
}


class SecretStore:
    def __init__(self, path: str, log) -> None:
        self.path = path
        self.log = log
        self.data: Dict[str, str] = {}

    def load(self) -> "SecretStore":
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    self.data = json.load(fh)
            except (OSError, ValueError) as exc:
                raise InstallError(f"Cannot read {self.path}: {exc}",
                                   remediation="Restore the file from your backup. Without it the "
                                               "running services cannot be managed.")
        for v in self.data.values():
            self.log.add_secret(v)
        return self

    def save(self) -> None:
        d = os.path.dirname(self.path)
        os.makedirs(d, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".secrets-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def get(self, key: str) -> str:
        if key not in self.data:
            raise InstallError(f"internal error: secret {key!r} was never generated",
                               remediation="Re-run with --force secrets.")
        return self.data[key]

    def maybe(self, key: str) -> Optional[str]:
        return self.data.get(key)

    def put(self, key: str, value: str) -> None:
        """Store a value produced elsewhere (a token returned by an API) and persist it
        before it is used, so a crash can never lose it."""
        self.log.add_secret(value)
        if self.data.get(key) != value:
            self.data[key] = value
            self.save()

    def ensure(self, key: str, value: Optional[str] = None) -> str:
        if key not in self.data:
            self.put(key, value if value else GENERATORS[key]())
        return self.data[key]

    def ensure_all(self) -> None:
        missing = [k for k in GENERATORS if k not in self.data]
        for k in missing:
            self.data[k] = GENERATORS[k]()
            self.log.add_secret(self.data[k])
        if missing:
            self.save()
