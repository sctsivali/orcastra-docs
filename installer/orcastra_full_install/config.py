"""Answer keys, validation, and the rules for what may change after instances exist.

Every setting has one upper-case key that works the same in three places: a CLI flag
(`--ip-vault`), the answer file (`IP_VAULT=...`) and the environment (`ORCASTRA_IP_VAULT`).
Precedence is CLI > answer file > environment > wizard/default.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Dict, Iterable, List, Optional

from orcastra_core.errors import ConfigError

from .topology import (CMP_COMPOSE_SHA256, CMP_RELEASES, CMP_VERSION_DEFAULT, IMAGE_DEFAULT,
                       PROJECT_DEFAULT, PUBLIC_PORTS, ROLES, SIZING)

# key -> (default, help). None means "ask in the wizard / detect".
FIELDS: Dict[str, tuple] = {
    "INSTANCE_TYPE": ("vm", "vm or container"),
    "LXD_PROJECT": (PROJECT_DEFAULT, "LXD project that holds the instances"),
    "STORAGE_POOL": (None, "existing storage pool, or a new pool name"),
    "POOL_DRIVER": (None, "driver for a new pool: zfs, btrfs or dir"),
    "POOL_SIZE_GIB": (None, "size of a new loop-backed pool"),
    "POOL_SOURCE": ("", "block device or path for a new pool (empty = loop file)"),
    "NETWORK": (None, "existing managed bridge, or a new bridge name"),
    "NETWORK_SUBNET": (None, "gateway/prefix for a new bridge, for example 10.77.0.1/24"),
    "IP_VAULT": (None, "private IPv4 of orca-vault"),
    "IP_AUTHENTIK": (None, "private IPv4 of orca-authentik"),
    "IP_OPENSEARCH": (None, "private IPv4 of orca-opensearch"),
    "IP_CMP": (None, "private IPv4 of orca-cmp"),
    "SIZING": ("compact", "compact, production or custom"),
    "HOST_ADDRESS": (None, "host address browsers use"),
    "PORT_AUTHENTIK": ("9000", "host port for Authentik"),
    "PORT_CMP": ("4321", "host port for the CMP web app"),
    "PORT_API": ("8765", "host port for the CMP API"),
    "PORT_LOGS": ("5601", "host port for OpenSearch Dashboards"),
    "ADMIN_EMAIL": (None, "email of the akadmin account"),
    "ADMIN_PASSWORD": ("", "akadmin password (empty = generate)"),
    "CMP_VERSION": (CMP_VERSION_DEFAULT, "Orcastra CMP release: latest or one of "
                    + ", ".join(CMP_RELEASES)),
    "IMAGE": (IMAGE_DEFAULT, "LXD image for the instances"),
    "FIX_HOST_FIREWALL": ("ask", "yes, no or ask: let the installer fix host Docker/ufw rules"),
}
for _r in ROLES:
    for _k in ("CPU", "MEM", "DISK"):
        FIELDS[f"{_k}_{_r.upper()}"] = ("", f"{_k.lower()} override for orca-{_r}")

# Fixed once instances exist: changing them would orphan or silently misconfigure what
# is already running. HOST_ADDRESS and the PORT_* keys can change (re-render path).
FROZEN = ("INSTANCE_TYPE", "LXD_PROJECT", "STORAGE_POOL", "NETWORK", "IP_VAULT",
          "IP_AUTHENTIK", "IP_OPENSEARCH", "IP_CMP", "IMAGE", "CMP_VERSION")
RERENDER = ("HOST_ADDRESS", "PORT_AUTHENTIK", "PORT_CMP", "PORT_API", "PORT_LOGS")

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


# -- validators: return an error string, or None when the value is fine ---------------------
def v_ipv4(value: str) -> Optional[str]:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return f"{value!r} is not an IP address"
    if ip.version != 4:
        return "only IPv4 is supported"
    return None


def v_subnet(value: str) -> Optional[str]:
    """A bridge address in gateway/prefix form, private, prefix /16../28."""
    try:
        iface = ipaddress.ip_interface(value)
    except ValueError:
        return f"{value!r} is not in gateway/prefix form (for example 10.77.0.1/24)"
    if iface.version != 4:
        return "only IPv4 is supported"
    if not iface.network.is_private:
        return "use a private range (10/8, 172.16/12 or 192.168/16)"
    if not 16 <= iface.network.prefixlen <= 28:
        return "prefix must be between /16 and /28"
    if iface.ip in (iface.network.network_address, iface.network.broadcast_address):
        return "the gateway cannot be the network or broadcast address"
    return None


def v_ip_in(subnet: str, gateway: Optional[str] = None):
    net = ipaddress.ip_interface(subnet).network if "/" in subnet else ipaddress.ip_network(subnet)

    def check(value: str) -> Optional[str]:
        err = v_ipv4(value)
        if err:
            return err
        ip = ipaddress.ip_address(value)
        if ip not in net:
            return f"{value} is outside {net}"
        if ip in (net.network_address, net.broadcast_address):
            return f"{value} is the network or broadcast address of {net}"
        if gateway and value == gateway:
            return f"{value} is the bridge gateway"
        return None
    return check


def v_port(value: str) -> Optional[str]:
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        return f"{value!r} is not a TCP port"
    return None


def v_email(value: str) -> Optional[str]:
    return None if _EMAIL.match(value) else f"{value!r} is not an email address"


def v_password(value: str) -> Optional[str]:
    if len(value) < 12:
        return "use at least 12 characters"
    if value.strip() != value:
        return "leading or trailing spaces are not allowed"
    return None


def v_name(value: str) -> Optional[str]:
    return None if _NAME.match(value) else \
        f"{value!r} must be lower-case letters, digits and dashes, starting with a letter"


_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*(:[A-Za-z0-9][A-Za-z0-9._/-]*)?$")


def v_image(value: str) -> Optional[str]:
    return None if _IMAGE.match(value) else f"{value!r} is not an LXD image reference"


def v_source(value: str) -> Optional[str]:
    if value and not (value.startswith("/") and re.match(r"^/[A-Za-z0-9._/-]+$", value)):
        return f"{value!r} must be an absolute path to a block device or directory"
    return None


def v_choice(*options: str):
    def check(value: str) -> Optional[str]:
        return None if value in options else f"choose one of: {', '.join(options)}"
    return check


def v_int_range(lo: int, hi: int):
    def check(value: str) -> Optional[str]:
        if not value.isdigit() or not lo <= int(value) <= hi:
            return f"enter a whole number between {lo} and {hi}"
        return None
    return check


def v_version(value: str) -> Optional[str]:
    if value == "latest" or value in CMP_COMPOSE_SHA256:
        return None
    return (f"this installer ships the compose for: {', '.join(sorted(CMP_COMPOSE_SHA256))}. "
            "Use a newer installer release for other versions.")


# -- merging --------------------------------------------------------------------------------
def merge(cli: Dict[str, Optional[str]], answers: Dict[str, str]) -> Dict[str, str]:
    """Return {KEY: value} for every key that has a value from CLI, answers/env or a
    default. Keys still missing are left for the wizard. Unknown answer keys are refused,
    since a typo would otherwise be ignored silently."""
    unknown = sorted(k for k in answers if k not in FIELDS and k not in _CONTROL_KEYS)
    if unknown:
        raise ConfigError(f"Unknown answer key(s): {', '.join(unknown)}",
                          remediation="Check the spelling against `--help`; valid keys: "
                                      + ", ".join(sorted(FIELDS)))
    out: Dict[str, str] = {}
    for key, (default, _) in FIELDS.items():
        if cli.get(key) not in (None, ""):
            out[key] = str(cli[key])
        elif key in answers and answers[key] != "":
            out[key] = answers[key]
        elif default is not None:
            out[key] = default
    return out


# answer keys that steer the run rather than describe the deployment
_CONTROL_KEYS = {"ASSUME_YES", "NON_INTERACTIVE", "VERBOSE"}


def sizes_from(values: Dict[str, str]) -> Dict[str, Dict[str, int]]:
    """Start from the chosen profile and apply any CPU_/MEM_/DISK_ overrides."""
    profile = values.get("SIZING", "compact")
    base = SIZING["compact" if profile == "custom" else profile]
    out = {r: dict(base[r]) for r in ROLES}
    for r in ROLES:
        for k, field in (("cpu", "CPU"), ("mem", "MEM"), ("disk", "DISK")):
            raw = values.get(f"{field}_{r.upper()}", "")
            if raw:
                if not raw.isdigit() or int(raw) < 1:
                    raise ConfigError(f"{field}_{r.upper()}={raw!r} is not a positive integer")
                out[r][k] = int(raw)
    return out


def check_unique(values: Dict[str, str], keys: Iterable[str], what: str) -> None:
    seen: Dict[str, str] = {}
    for k in keys:
        v = values.get(k)
        if v is None:
            continue
        if v in seen:
            raise ConfigError(f"{what} {v} is used twice ({seen[v]} and {k}).")
        seen[v] = k


def frozen_changes(previous: Dict[str, str], current: Dict[str, str]) -> List[str]:
    """Keys whose value differs from what the existing instances were built with."""
    return [k for k in FROZEN if k in previous and k in current and previous[k] != current[k]]


def public_ports(values: Dict[str, str]) -> Dict[str, int]:
    return {svc: int(values[f"PORT_{svc.upper()}"]) for svc in PUBLIC_PORTS}
