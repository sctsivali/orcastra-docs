"""FullContext: the one object every phase receives. Holds the resolved answers, the host
paths, the shared log/proc/state/prompt handles, the secret store and LXD helpers."""
from __future__ import annotations

import os
from typing import Dict, Optional

from orcastra_core.proc import Proc
from orcastra_core.prompt import Prompter
from orcastra_core.state import State

from . import topology as T
from .config import public_ports, sizes_from
from .lxd import Lxd
from .remote import Remote
from .secrets import SecretStore


class FullContext:
    def __init__(self, *, deploy_dir: str, flags, log, proc: Proc, state: State,
                 prompt: Prompter, interactive: bool, dry_run: bool,
                 values: Dict[str, str]) -> None:
        self.deploy_dir = deploy_dir
        self.flags = flags
        self.log = log
        self.proc = proc
        self.state = state
        self.prompt = prompt
        self.interactive = interactive
        self.dry_run = dry_run
        self.values = values                      # KEY -> value, see config.FIELDS
        self.secrets = SecretStore(self.path("secrets.json"), log)
        self.facts: Dict[str, object] = {}        # discovered at runtime (host, LXD)
        self.answered: set = set(values)            # keys the operator settled (see cli)
        self._lxd: Optional[Lxd] = None

    # -- paths ----------------------------------------------------------------------------
    def path(self, *parts: str) -> str:
        return os.path.join(self.deploy_dir, *parts)

    @property
    def ssh_dir(self) -> str:
        return self.path("ssh")

    @property
    def pki_dir(self) -> str:
        return self.path("pki")

    @property
    def vault_init_path(self) -> str:
        return self.path("vault-init.json")

    # -- LXD ------------------------------------------------------------------------------
    @property
    def lxd(self) -> Lxd:
        if self._lxd is None or self._lxd.project != self.project:
            self._lxd = Lxd(self.proc, self.log, self.project)
        return self._lxd

    def remote(self, role: str) -> Remote:
        return Remote(self.lxd, T.name(role))

    # -- resolved settings ----------------------------------------------------------------
    def v(self, key: str, default: Optional[str] = None) -> str:
        val = self.values.get(key, default)
        if val is None:
            raise KeyError(key)
        return val

    @property
    def project(self) -> str:
        return self.values.get("LXD_PROJECT", T.PROJECT_DEFAULT)

    @property
    def is_vm(self) -> bool:
        return self.values.get("INSTANCE_TYPE", "vm") == "vm"

    def ip(self, role: str) -> str:
        return self.v(f"IP_{role.upper()}")

    @property
    def sizes(self) -> Dict[str, Dict[str, int]]:
        return sizes_from(self.values)

    @property
    def ports(self) -> Dict[str, int]:
        return public_ports(self.values)

    @property
    def host_address(self) -> str:
        return self.v("HOST_ADDRESS")

    def public_url(self, service: str) -> str:
        return f"http://{self.host_address}:{self.ports[service]}"

    @property
    def issuer(self) -> str:
        return f"{self.public_url('authentik')}/application/o/{T.ISSUER_SLUG}/"

    @property
    def bridge_gateway(self) -> str:
        return str(self.facts.get("bridge_gateway") or self.values.get("NETWORK_SUBNET", "").split("/")[0])
