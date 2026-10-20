"""Answer keys, validation, and the rules for what may change after instances exist.

Every setting has one upper-case key that works the same in three places: a CLI flag
(`--ip-vault`), the answer file (`IP_VAULT=...`) and the environment (`ORCASTRA_IP_VAULT`).
Precedence is CLI > answer file > environment > wizard/default.
"""
from __future__ import annotations

import base64
import ipaddress
import json
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
    "PUBLIC_DOMAIN": ("", "base domain for HTTPS through a Cloudflare Tunnel (empty or none = off)"),
    "DOMAIN_CMP": (None, "public hostname of the CMP (default app.<PUBLIC_DOMAIN>)"),
    "DOMAIN_API": (None, "public hostname of the CMP API (default api.<PUBLIC_DOMAIN>)"),
    "DOMAIN_SSO": (None, "public hostname of Authentik (default sso.<PUBLIC_DOMAIN>)"),
    "DOMAIN_LOGS": (None, "public hostname of OpenSearch Dashboards (default logs.<PUBLIC_DOMAIN>)"),
    "CLOUDFLARE_TUNNEL_TOKEN": ("", "Cloudflare Tunnel token (answer file or environment only)"),
    "ORCAHUB_CLIENT_ID": ("", "OrcaHub federation client ID (needs PUBLIC_DOMAIN)"),
    "ORCAHUB_DISCOVERY_URL": ("", "OrcaHub federation discovery URL (.well-known/openid-configuration)"),
    "ORCAHUB_CLIENT_SECRET": ("", "OrcaHub federation client secret (answer file or environment only)"),
}
for _r in ROLES:
    for _k in ("CPU", "MEM", "DISK"):
        FIELDS[f"{_k}_{_r.upper()}"] = ("", f"{_k.lower()} override for orca-{_r}")

# Fixed once instances exist: changing them would orphan or silently misconfigure what
# is already running. HOST_ADDRESS and the PORT_* keys can change (re-render path).
FROZEN = ("INSTANCE_TYPE", "LXD_PROJECT", "STORAGE_POOL", "NETWORK", "IP_VAULT",
          "IP_AUTHENTIK", "IP_OPENSEARCH", "IP_CMP", "IMAGE", "CMP_VERSION")
# Settings that may change on a built deployment, each with the phases that re-apply it
# (verify and summary always run). Unset and "" compare equal, so a key added by a newer
# installer is no change, while setting it on a deployment that never had it is one.
_URLS = ("firewall", "authentik", "cmp", "exposure")
_DOMAIN = ("authentik", "opensearch", "cmp", "exposure")
RERENDER = {
    "HOST_ADDRESS": _URLS, "PORT_AUTHENTIK": _URLS, "PORT_CMP": _URLS, "PORT_API": _URLS,
    "PORT_LOGS": _URLS,
    "PUBLIC_DOMAIN": _DOMAIN, "DOMAIN_CMP": _DOMAIN, "DOMAIN_API": _DOMAIN,
    "DOMAIN_SSO": _DOMAIN, "DOMAIN_LOGS": _DOMAIN,
}

# Never a command-line flag (process list) and never in state.json: kept in secrets.json.
SECRET_KEYS = ("ADMIN_PASSWORD", "CLOUDFLARE_TUNNEL_TOKEN", "ORCAHUB_CLIENT_SECRET")

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


_FQDN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def v_fqdn(value: str) -> Optional[str]:
    if not _FQDN.match(value):
        return f"{value!r} is not a lower-case DNS name such as app.example.com"
    try:
        ipaddress.ip_address(value)
        return "use a DNS name, not an IP address"
    except ValueError:
        return None


def tunnel_token(text: str) -> str:
    """The token from whatever was pasted: the bare token or Cloudflare's whole
    `cloudflared service install <token>` / `tunnel run --token <token>` line."""
    words = [w for w in text.split() if w.startswith("eyJ")]
    return words[-1] if words else text.strip()


def tunnel_id(token: str) -> Optional[str]:
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), validate=True)
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(doc, dict) or not all(isinstance(doc.get(k), str) and doc[k] for k in "ats"):
        return None
    return doc["t"] if re.match(r"^[0-9a-f-]{36}$", doc["t"]) else None


def v_tunnel_token(value: str) -> Optional[str]:
    if tunnel_id(tunnel_token(value)) is None:
        return ("not a Cloudflare Tunnel token: copy the long value starting with eyJ from the "
                "tunnel's install command (Zero Trust, Networks, Tunnels)")
    return None


def v_https_url(value: str) -> Optional[str]:
    if not re.match(r"^https://[a-z0-9.-]+(:[0-9]+)?/\S*$", value):
        return f"{value!r} is not an https URL"
    return None


def is_off(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("", "none", "off")


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


def rerender_keys(previous: Dict[str, str], current: Dict[str, str]) -> List[str]:
    """RERENDER keys whose value differs from what the deployment was last built with."""
    return [k for k in RERENDER if (previous.get(k) or "") != (current.get(k) or "")]


def rerender_phases(keys: Iterable[str]) -> List[str]:
    return sorted({p for k in keys for p in RERENDER[k]}) + ["verify", "summary"]


def public_ports(values: Dict[str, str]) -> Dict[str, int]:
    return {svc: int(values[f"PORT_{svc.upper()}"]) for svc in PUBLIC_PORTS}
