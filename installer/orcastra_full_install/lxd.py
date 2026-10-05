"""Thin wrapper over the `lxc` CLI.

The CLI (rather than the REST socket) keeps snap and non-snap LXD identical and reuses the
operator's own client config. Reads go through `lxc query` so they come back as JSON, and
writes use the regular sub-commands so errors read the way an operator expects.
"""
from __future__ import annotations

import json
import shutil
import urllib.parse
from typing import Any, Dict, List, Optional

from orcastra_core.errors import InstallError
from orcastra_core.proc import Proc, Result


class LxdError(InstallError):
    """An LXD API call or CLI command failed."""


class Lxd:
    def __init__(self, proc: Proc, log, project: str, lxc: Optional[str] = None) -> None:
        self.proc = proc
        self.log = log
        self.project = project
        self.lxc = lxc or shutil.which("lxc") or "/snap/bin/lxc"
        # GET results are cached until the next write: on a busy host a single API call can
        # take tens of seconds, and the wizard asks the same questions many times
        self._cache: Dict[str, Any] = {}
        self._project_exists: Optional[bool] = None

    # -- primitives -----------------------------------------------------------------------
    def cmd(self, *args: str, project: bool = True, mutating: bool = True,
            input: Optional[str] = None, timeout: Optional[int] = 600) -> Result:
        argv = [self.lxc, *args]
        if project:
            argv += ["--project", self.project]
        if mutating:
            self._cache.clear()
            self._project_exists = None
        return self.proc.run(argv, input=input, mutating=mutating, timeout=timeout)

    def must(self, *args: str, what: str, project: bool = True,
             input: Optional[str] = None, timeout: Optional[int] = 600) -> Result:
        res = self.cmd(*args, project=project, input=input, timeout=timeout)
        if not res.ok:
            raise LxdError(f"{what} failed: {(res.err or res.out).strip()[-400:]}",
                           remediation="Check `lxc` output above; re-run the installer to resume.")
        return res

    def query(self, path: str, *, project: Optional[str] = None,
              params: Optional[Dict[str, str]] = None, fresh: bool = False) -> Any:
        """GET an API path. Returns parsed metadata, or None on 404. Instance paths in a
        project that does not exist yet answer None without asking LXD."""
        proj = project or self.project
        q = dict(params or {})
        if "all-projects" not in q:
            q["project"] = proj
            if proj != "default" and path.startswith("/1.0/instances") and not self._has_project(proj):
                return [] if path == "/1.0/instances" else None
        url = path + ("&" if "?" in path else "?") + urllib.parse.urlencode(q)
        if not fresh and url in self._cache:
            return self._cache[url]
        res = None
        for _ in range(3):
            res = self.proc.run([self.lxc, "query", url], timeout=120)
            if res.rc != 124:
                break
            self.log.detail(f"LXD is slow to answer {path}, retrying")
        if res.ok:
            data = json.loads(res.out) if res.out.strip() else None
            self._cache[url] = data
            return data
        if "not found" in res.err.lower() or "404" in res.err:
            self._cache[url] = None
            return None
        raise LxdError(f"LXD query {path} failed: {res.err.strip()[-300:]}",
                       remediation="Check that the LXD daemon is healthy (`lxc list` answers), "
                                   "then re-run; the installer resumes.")

    def _has_project(self, name: str) -> bool:
        if name == self.project and self._project_exists is not None:
            return self._project_exists
        found = self.query(f"/1.0/projects/{name}", project="default") is not None
        if name == self.project:
            self._project_exists = found
        return found

    # -- server / projects ----------------------------------------------------------------
    def server(self) -> Dict[str, Any]:
        return self.query("/1.0", project="default") or {}

    def project_info(self, name: str) -> Optional[Dict[str, Any]]:
        return self.query(f"/1.0/projects/{name}", project="default")

    def profile(self, name: str) -> Optional[Dict[str, Any]]:
        return self.query(f"/1.0/profiles/{name}")

    # -- instances ------------------------------------------------------------------------
    def instance(self, name: str) -> Optional[Dict[str, Any]]:
        return self.query(f"/1.0/instances/{name}")

    def instance_state(self, name: str) -> Optional[Dict[str, Any]]:
        return self.query(f"/1.0/instances/{name}/state")

    def instances(self, *, all_projects: bool = False) -> List[Dict[str, Any]]:
        params = {"recursion": "1"}
        if all_projects:
            params["all-projects"] = "true"
        return self.query("/1.0/instances", params=params) or []

    def status(self, name: str) -> Optional[str]:
        inst = self.query(f"/1.0/instances/{name}", fresh=True)
        return inst.get("status") if inst else None

    # -- networks -------------------------------------------------------------------------
    def networks(self) -> List[Dict[str, Any]]:
        return self.query("/1.0/networks", project="default", params={"recursion": "1"}) or []

    def network(self, name: str) -> Optional[Dict[str, Any]]:
        return self.query(f"/1.0/networks/{name}", project="default")

    def leases(self, network: str) -> List[Dict[str, Any]]:
        """Leases across every project using the network (shared bridges list them all)."""
        rows = self.query(f"/1.0/networks/{network}/leases", project="default",
                          params={"all-projects": "true"})
        if rows is None:
            rows = self.query(f"/1.0/networks/{network}/leases", project="default") or []
        return rows

    def forwards(self, network: str) -> List[Dict[str, Any]]:
        return self.query(f"/1.0/networks/{network}/forwards", project="default",
                          params={"recursion": "1"}) or []

    def all_forwards(self) -> List[Dict[str, Any]]:
        out = []
        for net in self.networks():
            if net.get("managed"):
                for fwd in self.forwards(net["name"]):
                    out.append(dict(fwd, network=net["name"]))
        return out

    # -- storage --------------------------------------------------------------------------
    def pools(self) -> List[Dict[str, Any]]:
        return self.query("/1.0/storage-pools", project="default", params={"recursion": "1"}) or []

    def pool_resources(self, name: str) -> Dict[str, Any]:
        return self.query(f"/1.0/storage-pools/{name}/resources", project="default") or {}

    def host_resources(self) -> Dict[str, Any]:
        return self.query("/1.0/resources", project="default") or {}

    # -- images ---------------------------------------------------------------------------
    def image_reachable(self, image: str) -> bool:
        """True when the image alias resolves on its remote (needs network to the remote)."""
        res = self.cmd("image", "info", image, project=False, mutating=False, timeout=90)
        return res.ok
