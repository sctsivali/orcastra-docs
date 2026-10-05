"""Resource and reachability checks for the chosen plan, run at the end of the wizard so a
host that cannot hold the deployment fails before anything is created."""
from __future__ import annotations

from orcastra_core.errors import PreflightError

from . import hostnet
from . import topology as T


def check_plan(ctx) -> None:
    sizes = ctx.sizes
    problems = []
    existing = {r for r in T.ROLES if ctx.lxd.instance(T.name(r)) is not None}
    pending = [r for r in T.ROLES if r not in existing]

    mem_need = sum(sizes[r]["mem"] for r in pending)
    mem_avail = hostnet.mem_available_gib()
    mem_total = hostnet.mem_total_gib()
    if pending:
        if mem_need > mem_total:
            problems.append(f"the new instances need {mem_need} GiB RAM, the host has "
                            f"{mem_total:.1f} GiB in total")
        elif mem_need > mem_avail:
            ctx.log.warn(f"The new instances may use up to {mem_need} GiB RAM, {mem_avail:.1f} GiB "
                         "is free now. They start, but the host will be under memory pressure.")
        else:
            ctx.log.ok(f"Memory: {mem_need} GiB planned, {mem_avail:.1f} GiB available")

    cpus = (ctx.lxd.host_resources().get("cpu") or {}).get("total", 0)
    cpu_need = sum(sizes[r]["cpu"] for r in T.ROLES)
    if cpus:
        biggest = max(sizes[r]["cpu"] for r in T.ROLES)
        if biggest > cpus:
            problems.append(f"an instance asks for {biggest} vCPU, the host has {cpus} threads")
        elif cpu_need > cpus:
            ctx.log.warn(f"{cpu_need} vCPU planned on {cpus} host threads (overcommitted, allowed).")
        else:
            ctx.log.ok(f"CPU: {cpu_need} vCPU planned, {cpus} host threads")

    pool = ctx.values["STORAGE_POOL"]
    disk_need = sum(sizes[r]["disk"] for r in pending)
    res = ctx.lxd.pool_resources(pool) if any(p["name"] == pool for p in ctx.lxd.pools()) else {}
    space = res.get("space") or {}
    if space.get("total"):
        free = (space["total"] - space.get("used", 0)) / 1024 ** 3
        driver = next((p.get("driver") for p in ctx.lxd.pools() if p["name"] == pool), "")
        if disk_need > free:
            msg = (f"pool {pool} has {free:.0f} GiB free, the new instances may grow to "
                   f"{disk_need} GiB")
            if driver == "dir":
                problems.append(msg)
            else:
                ctx.log.warn(msg + " (thin-provisioned, allowed but watch usage)")
        else:
            ctx.log.ok(f"Storage: {disk_need} GiB planned on {pool}, {free:.0f} GiB free")

    if ctx.is_vm and not ctx.facts.get("kvm"):
        problems.append("virtual machines need /dev/kvm on the host")

    if pending:
        image = ctx.values.get("IMAGE", T.IMAGE_DEFAULT)
        if ctx.lxd.image_reachable(image):
            ctx.log.ok(f"Image {image} is reachable")
        else:
            problems.append(f"image {image} cannot be resolved (no route to the image server?)")

    from . import registry
    ver = ctx.values["CMP_VERSION"]
    for part in ("backend", "frontend"):
        found = registry.image_exists(f"{part}-{ver}")
        if found is False:
            problems.append(f"image {T.CMP_REGISTRY_REPO}:{part}-{ver} does not exist on Docker Hub")
        elif found is None:
            ctx.log.warn(f"Could not reach Docker Hub to confirm {part}-{ver} exists (will retry at pull time).")
    if not problems:
        ctx.log.ok(f"Release images {T.CMP_REGISTRY_REPO}:{{backend,frontend}}-{ver} exist")

    if problems:
        raise PreflightError("The plan does not fit this host:\n  - " + "\n  - ".join(problems),
                             remediation="Pick a smaller sizing, free resources, or fix the image "
                                         "server access, then re-run.")
