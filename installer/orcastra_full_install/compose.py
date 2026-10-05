"""docker compose helpers run inside an instance: pull with retry (registries and DNS in
fresh instances are flaky, and Docker Hub rate-limits anonymous pulls), up, and a health
wait that reports the failing service with its log tail."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Dict, Iterable, List, Optional

from orcastra_core.errors import DockerError


def config_hash(bodies: Iterable[str]) -> str:
    """Fingerprint of the rendered files a stack runs with. Compose does not notice when a
    bind-mounted file changes, so a phase compares this with the hash of the last config
    that came up healthy and force-recreates the containers when it differs."""
    h = hashlib.sha256()
    for b in bodies:
        h.update(hashlib.sha256(b.encode("utf-8")).digest())
    return h.hexdigest()


def needs_recreate(ctx, stack: str, digest: str) -> bool:
    return (ctx.state.artifact("config_hash") or {}).get(stack) != digest


def mark_running(ctx, stack: str, digest: str) -> None:
    hashes = dict(ctx.state.artifact("config_hash") or {})
    hashes[stack] = digest
    ctx.state.set_artifact("config_hash", hashes)


def _cd(directory: str, project: str, env_file: str = ".env") -> str:
    return f"cd {directory} && docker compose -p {project} --env-file {env_file}"


def pull(remote, directory: str, project: str, *, compose_file: str = "", what: str = "") -> None:
    f = f" -f {compose_file}" if compose_file else ""
    script = (f"for i in 1 2 3 4 5 6; do {_cd(directory, project)}{f} pull -q && exit 0; "
              "sleep $((i * 10)); done; exit 1")
    res = remote.run(script, check=False, timeout=3600, what=what)
    if not res.ok:
        tail = (res.err or res.out)[-600:]
        hint = ""
        if "toomanyrequests" in tail or "rate limit" in tail.lower():
            hint = ("Docker Hub's anonymous pull limit was hit. Wait a few hours, or run "
                    "`docker login` inside the instance, then re-run the installer.")
        raise DockerError(f"Image pull failed on {remote.name}: {tail}",
                          remediation=hint or "Check DNS and outbound HTTPS from the instance, then re-run.")


def up(remote, directory: str, project: str, *, compose_file: str = "", services: str = "",
       recreate: bool = False) -> None:
    f = f" -f {compose_file}" if compose_file else ""
    flag = " --force-recreate" if recreate else ""
    remote.run(f"{_cd(directory, project)}{f} up -d --remove-orphans{flag} {services}".rstrip(),
               timeout=1200, what=f"compose up ({project})")


def ps(remote, directory: str, project: str, compose_file: str = "") -> List[Dict]:
    f = f" -f {compose_file}" if compose_file else ""
    res = remote.run(f"{_cd(directory, project)}{f} ps -a --format json", check=False, timeout=60)
    rows: List[Dict] = []
    text = res.out.strip()
    if not text:
        return rows
    try:
        data = json.loads(text)
        rows = data if isinstance(data, list) else [data]
    except ValueError:
        for line in text.splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def wait_healthy(remote, directory: str, project: str, services: List[str], *, timeout: int = 600,
                 compose_file: str = "", running_only: Optional[List[str]] = None) -> None:
    """Wait until each service is healthy (or running, for the ones without a healthcheck)."""
    running_only = running_only or []
    deadline = time.monotonic() + timeout
    state: Dict[str, str] = {}
    while time.monotonic() < deadline:
        state = {}
        for row in ps(remote, directory, project, compose_file):
            svc = row.get("Service", "")
            health = row.get("Health", "") or ""
            st = row.get("State", "")
            state[svc] = health if health else st
        bad = [s for s in services if not (state.get(s) == "healthy"
                                           or (s in running_only and state.get(s) == "running"))]
        if not bad:
            return
        if any(state.get(s) in ("exited", "dead") for s in bad):
            break
        time.sleep(10)
    bad = [s for s in services if state.get(s) not in ("healthy", "running")] or services
    logs = remote.run(f"{_cd(directory, project)} logs --tail 40 {' '.join(bad)}",
                      check=False, timeout=60).out[-1500:]
    raise DockerError(f"Services not healthy on {remote.name}: "
                      + ", ".join(f"{s}={state.get(s, 'missing')}" for s in bad) + "\n" + logs,
                      remediation="Fix the error in the log above, then re-run the installer.")
