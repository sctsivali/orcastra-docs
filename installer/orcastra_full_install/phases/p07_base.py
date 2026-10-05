"""Phase 7 - Base packages. Docker CE from Docker's apt repository on the three instances
that run containers (docs/deployment/index.md), with log rotation and a fixed address
pool for Docker networks. orca-vault runs Vault natively and gets no Docker."""
from __future__ import annotations

import json

from orcastra_core.errors import InstallError

from .. import topology as T

TITLE = "Docker engine on authentik, opensearch and cmp"

DOCKER_ROLES = ("authentik", "opensearch", "cmp")

DAEMON_JSON = json.dumps({
    "log-driver": "json-file",
    "log-opts": {"max-size": "50m", "max-file": "5"},
    "default-address-pools": [{"base": T.DOCKER_POOL, "size": 24}],
}, indent=2) + "\n"

_INSTALL = r"""
if ! command -v docker >/dev/null 2>&1; then
  . /etc/os-release
  install -m 0755 -d /etc/apt/keyrings
  for i in 1 2 3 4 5; do
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && break
    sleep $((i * 3))
  done
  chmod a+r /etc/apt/keyrings/docker.asc
  cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  for i in 1 2 3 4 5; do
    apt-get update -q && apt-get install -y -q docker-ce docker-ce-cli containerd.io \
      docker-buildx-plugin docker-compose-plugin && break
    sleep $((i * 5))
  done
fi
command -v docker >/dev/null
"""

_CHECK = r"""
systemctl enable --now docker >/dev/null 2>&1
systemctl is-active --quiet docker
echo "engine=$(docker version --format '{{.Server.Version}}')"
echo "compose=$(docker compose version --short)"
echo "driver=$(docker info --format '{{.Driver}}')"
"""


def _vtuple(v: str) -> tuple:
    return tuple(int("".join(c for c in p if c.isdigit()) or 0) for p in v.lstrip("v").split(".")[:3])


def run(ctx) -> None:
    for role in DOCKER_ROLES:
        r = ctx.remote(role)
        r.run(_INSTALL, what="install Docker", timeout=1800)
        changed = not r.same_content("/etc/docker/daemon.json", DAEMON_JSON)
        if changed:
            r.write("/etc/docker/daemon.json", DAEMON_JSON, what="docker daemon.json")
            r.run("systemctl restart docker", what="restart docker", timeout=300)
        out = r.run(_CHECK, what="check Docker", timeout=300).out
        info = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        if _vtuple(info.get("engine", "0")) < (24, 0) or _vtuple(info.get("compose", "0")) < (2, 20):
            raise InstallError(f"{T.name(role)} has Docker {info.get('engine')} / compose "
                               f"{info.get('compose')}, need Engine 24+ and Compose 2.20+.")
        if info.get("driver") == "vfs":
            ctx.log.warn(f"{T.name(role)}: Docker fell back to the vfs storage driver, which copies "
                         "every layer and uses a lot of disk.")
        ctx.log.ok(f"{T.name(role)}: Docker {info.get('engine')}, compose {info.get('compose')}, "
                   f"storage {info.get('driver')}")
