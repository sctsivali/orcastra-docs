"""Phase 4 - LXD resources. The project, and the storage pool and bridge when the wizard
chose new ones, then the `orcastra` profile (root disk, NIC, cloud-init). Every resource
this phase creates is recorded so uninstall removes exactly that and nothing else."""
from __future__ import annotations

import json
import os

from .. import cloudinit, hostfix
from .. import topology as T

TITLE = "LXD project, storage, network and profile"


def _record(ctx, kind: str, name: str) -> None:
    created = ctx.state.artifact("created") or {}
    created.setdefault(kind, [])
    if name not in created[kind]:
        created[kind].append(name)
    ctx.state.set_artifact("created", created)


def _project(ctx) -> None:
    if ctx.lxd.project_info(ctx.project):
        ctx.log.ok(f"Project {ctx.project} exists")
        return
    ctx.lxd.must("project", "create", ctx.project,
                 "-c", "features.images=false", "-c", "features.profiles=true",
                 "-c", "features.storage.volumes=true", "-c", "features.networks=false",
                 "-c", "user.orcastra=managed",
                 project=False, what=f"create project {ctx.project}")
    _record(ctx, "project", ctx.project)
    ctx.log.ok(f"Created project {ctx.project}")


def _pool(ctx) -> None:
    name = ctx.values["STORAGE_POOL"]
    if any(p["name"] == name for p in ctx.lxd.pools()):
        ctx.log.ok(f"Storage pool {name} exists")
        return
    driver = ctx.values.get("POOL_DRIVER") or "dir"
    args = ["storage", "create", name, driver]
    if ctx.values.get("POOL_SOURCE"):
        args.append(f"source={ctx.values['POOL_SOURCE']}")
    elif driver != "dir":
        args.append(f"size={ctx.values.get('POOL_SIZE_GIB', '200')}GiB")
    ctx.lxd.must(*args, project=False, what=f"create storage pool {name}", timeout=900)
    _record(ctx, "pool", name)
    ctx.log.ok(f"Created storage pool {name} ({driver})")


def _network(ctx) -> None:
    name = ctx.values["NETWORK"]
    if ctx.lxd.network(name):
        ctx.log.ok(f"Network {name} exists")
        return
    ctx.lxd.must("network", "create", name, f"ipv4.address={ctx.values['NETWORK_SUBNET']}",
                 "ipv4.nat=true", "ipv6.address=none", "user.orcastra=managed",
                 project=False, what=f"create network {name}")
    _record(ctx, "network", name)
    ctx.log.ok(f"Created bridge {name} ({ctx.values['NETWORK_SUBNET']}, NAT)")


def _profile(ctx) -> None:
    pub = open(os.path.join(ctx.ssh_dir, "id_ed25519.pub"), encoding="utf-8").read().strip()
    doc = {
        "description": "Orcastra CMP Full instances (managed by orcastra-full)",
        "config": {
            "cloud-init.user-data": cloudinit.user_data(pub, vm=ctx.is_vm),
            "boot.autostart": "true",
        },
        "devices": {
            "root": {"type": "disk", "path": "/", "pool": ctx.values["STORAGE_POOL"]},
            "eth0": {"type": "nic", "name": "eth0", "network": ctx.values["NETWORK"]},
        },
    }
    if not ctx.lxd.profile(T.PROFILE):
        ctx.lxd.must("profile", "create", T.PROFILE, what=f"create profile {T.PROFILE}")
        _record(ctx, "profile", T.PROFILE)
    # JSON is valid YAML, so `profile edit` takes the document as-is
    ctx.lxd.must("profile", "edit", T.PROFILE, input=json.dumps(doc), what="write profile")
    ctx.log.ok(f"Profile {T.PROFILE} ready (pool {ctx.values['STORAGE_POOL']}, "
               f"network {ctx.values['NETWORK']}, cloud-init)")


def run(ctx) -> None:
    _project(ctx)
    _pool(ctx)
    _network(ctx)
    hostfix.apply(ctx, ctx.values["NETWORK"])
    _profile(ctx)
