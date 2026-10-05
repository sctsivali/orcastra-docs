"""Fixed facts about the deployment shape: the four instances, their sizing profiles, the
ports each one serves, and the pinned versions of everything the installer pulls."""
from __future__ import annotations

from typing import Dict

# Launch order matters: the stores come up before the CMP that depends on them.
ROLES = ("vault", "authentik", "opensearch", "cmp")
PREFIX = "orca-"
PROJECT_DEFAULT = "orcastra"
PROFILE = "orcastra"
DEPLOY_DIR = "/var/lib/orcastra"
REMOTE_DIR = "/opt/orcastra"          # compose project dir inside each instance
COMPOSE_PROJECT = "orcastra"
TAG_KEY = "user.orcastra.role"        # set on every instance we create

IMAGE_DEFAULT = "ubuntu:24.04"
CMP_REGISTRY_REPO = "svlct/orcastra-dashboard"   # tags backend-<version>, frontend-<version>

PINS = {
    "authentik": "2025.10.3",
    "vault_apt": "1.21.4-1",
    "fluentbit_apt": "4.2.8",
    "opensearch": "3.5.0",
}

# Releases this installer can deploy, newest first, each with its embedded release compose
# (verified by sha256 before use). "latest" means the first entry. Releases before
# 1.0.0-RC4-hotfix1 are not offered: their frontend healthcheck follows the sign-in redirect
# to the public URL, so autoheal restarts the frontend in a loop.
CMP_COMPOSE_SHA256 = {
    "1.0.0-RC4-hotfix2": "708e22e40adf832148e6db1ee2483a8cc197ff514257b7aaa493606c96e70f40",
    "1.0.0-RC4-hotfix1": "708e22e40adf832148e6db1ee2483a8cc197ff514257b7aaa493606c96e70f40",
}
CMP_RELEASES = tuple(CMP_COMPOSE_SHA256)
CMP_LATEST = CMP_RELEASES[0]
CMP_VERSION_DEFAULT = "latest"


def resolve_version(value: str) -> str:
    return CMP_LATEST if value in ("", "latest") else value


# vCPU, memory GiB, root disk GiB. "production" is the guide's recommendation
# (docs/getting-started/prerequisites.md), "compact" fits a 16 GB lab host.
SIZING: Dict[str, Dict[str, Dict[str, int]]] = {
    "production": {
        "authentik": {"cpu": 2, "mem": 4, "disk": 40},
        "vault": {"cpu": 2, "mem": 2, "disk": 20},
        "opensearch": {"cpu": 4, "mem": 16, "disk": 100},
        "cmp": {"cpu": 4, "mem": 8, "disk": 60},
    },
    "compact": {
        "authentik": {"cpu": 2, "mem": 3, "disk": 20},
        "vault": {"cpu": 1, "mem": 1, "disk": 10},
        "opensearch": {"cpu": 2, "mem": 6, "disk": 40},
        "cmp": {"cpu": 2, "mem": 4, "disk": 30},
    },
}
MIN_SIZE = {
    "authentik": {"cpu": 1, "mem": 2, "disk": 15},
    "vault": {"cpu": 1, "mem": 1, "disk": 8},
    "opensearch": {"cpu": 2, "mem": 4, "disk": 30},
    "cmp": {"cpu": 2, "mem": 3, "disk": 25},
}

# Public (browser-facing) services and their in-instance ports. The host-side port is
# configurable, the target port is fixed by the component.
PUBLIC_PORTS = {
    "authentik": ("authentik", 9000),
    "cmp": ("cmp", 4321),
    "api": ("cmp", 8765),
    "logs": ("opensearch", 5601),
}

# Docker networks inside the instances are allocated from this pool, so the CMP backend can
# trust exactly its own proxies (TRUSTED_PROXY_CIDRS) instead of every RFC1918 range.
DOCKER_POOL = "172.20.0.0/14"

ISSUER_SLUG = "orcastra-dashboard"


def name(role: str) -> str:
    return PREFIX + role


def opensearch_heap_gib(mem_gib: int) -> int:
    """Half the instance memory, capped at 31 GiB (compressed oops), at least 1."""
    return max(1, min(31, mem_gib // 2))


def backend_limits(cpu: int, mem_gib: int) -> Dict[str, str]:
    """CMP backend container caps derived from the instance size, so a small instance never
    gets the compose default of 4g/2 CPUs (OOM or a refused `cpus:`)."""
    cpus = max(1, min(2, cpu - 1 if cpu > 2 else cpu))
    mem = max(1, min(4, mem_gib - 2))
    workers = max(1, min(4, cpus * 2))
    return {"BACKEND_MEM_LIMIT": f"{mem}g", "BACKEND_CPUS": str(cpus),
            "WEB_CONCURRENCY": str(workers)}
