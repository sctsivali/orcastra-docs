"""OpenSearch and OpenSearch Dashboards admin calls from the host. TLS to :9200 is verified
against the installer's private CA."""
from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List

from orcastra_core.errors import InstallError

from . import _blocks, bundled
from .httpapi import Client, HttpError

ACCESS_PATTERN = ("12ba9640-0014-11f1-b2de-a9a1dde61479", "orcastra-access-*")
AUDIT_PATTERN = ("34b2e040-0014-11f1-b2de-a9a1dde61479", "orcastra-audit-*")
ALL_PATTERN = ("orcastra-all-logs-pattern", "orcastra-*")
NDJSON = ("access-logs-dashboard-v3.ndjson", "audit-logs-dashboard-v3.ndjson",
          "logs-overview-dashboard.ndjson", "vault-audit-dashboard.ndjson")


POLICIES = ("orcastra-access-policy", "orcastra-app-policy", "orcastra-audit-policy",
            "vault-audit-policy", "security-auditlog-policy")


def _ca(ctx) -> str:
    return os.path.join(ctx.pki_dir, "root-ca.pem")


def client(ctx, user: str = "admin", password: str = None) -> Client:
    pw = password if password is not None else ctx.secrets.get("os_admin_password")
    return Client(f"https://{ctx.ip('opensearch')}:9200", basic=(user, pw), cafile=_ca(ctx), timeout=30)


def admin_cert_client(ctx) -> Client:
    """The security admin certificate: the only identity allowed to change system indices."""
    return Client(f"https://{ctx.ip('opensearch')}:9200", cafile=_ca(ctx), timeout=30,
                  cert=(os.path.join(ctx.pki_dir, "admin.pem"), os.path.join(ctx.pki_dir, "admin-key.pem")))


def dashboards(ctx) -> Client:
    return Client(f"http://{ctx.ip('opensearch')}:5601",
                  basic=("admin", ctx.secrets.get("os_admin_password")),
                  headers={"osd-xsrf": "true", "securitytenant": "global"}, timeout=60)


def health(ctx) -> Dict[str, Any]:
    return client(ctx).get("/_cluster/health") or {}


def ensure_fluentbit_user(ctx) -> bool:
    """Create or reset the API-managed `fluentbit` user. Skips when its password already
    works, so a re-run changes nothing."""
    pw = ctx.secrets.get("fluentbit_password")
    try:
        client(ctx, "fluentbit", pw).get("/_plugins/_security/authinfo")
        return False
    except HttpError as exc:
        if exc.status not in (401, 403):
            raise
    client(ctx).put("/_plugins/_security/api/internalusers/fluentbit",
                    {"password": pw, "backend_roles": ["log_writer"],
                     "description": "Fluent Bit forwarders on orca-vault and orca-cmp"})
    return True


def apply_guide_objects(ctx) -> List[str]:
    """The ingest pipeline, index templates, snapshot repository and ISM policies, in the
    guide's order. ISM policies cannot be PUT twice without seq_no, so existing ones are
    updated with their current seq_no/primary_term."""
    c = client(ctx)
    done = []
    for path, body in _blocks.OS_API:
        if path.startswith("_plugins/_ism/policies/"):
            try:
                cur = c.get("/" + path)
                q = f"?if_seq_no={cur['_seq_no']}&if_primary_term={cur['_primary_term']}"
                if cur.get("policy", {}).get("states") == body["policy"]["states"] and \
                        cur.get("policy", {}).get("ism_template", [{}])[0].get("index_patterns") == \
                        body["policy"]["ism_template"][0]["index_patterns"]:
                    done.append(path)
                    continue
                c.put("/" + path + q, body)
            except HttpError as exc:
                if exc.status != 404:
                    raise
                c.put("/" + path, body)
        else:
            c.put("/" + path, body)
        done.append(path)
    return done


def attach_security_auditlog(ctx) -> None:
    """The security plugin may create this month's audit index before its policy exists
    (vm3 Step 10, attachment timing). Adding a policy twice is refused, which is fine."""
    try:
        client(ctx).post("/_plugins/_ism/add/security-auditlog-*",
                         {"policy_id": "security-auditlog-policy"})
    except HttpError as exc:
        if exc.status not in (400, 404):
            raise


def zero_replicas(ctx) -> List[str]:
    """vm3 Step 10: every index with replicas (system indices included) gets 0, through the
    admin certificate, so a single node stays green."""
    rows = client(ctx).get("/_cat/indices?h=index,rep&expand_wildcards=all&format=json") or []
    changed = []
    adm = admin_cert_client(ctx)
    for r in rows:
        if int(r.get("rep") or 0) > 0:
            adm.put(f"/{r['index']}/_settings", {"index.number_of_replicas": 0})
            changed.append(r["index"])
    return changed


def _multipart(field: str, filename: str, data: bytes):
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
            f"filename=\"{filename}\"\r\nContent-Type: application/ndjson\r\n\r\n").encode() \
        + data + f"\r\n--{boundary}--\r\n".encode()
    return body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def import_dashboards(ctx) -> Dict[str, int]:
    """What scripts/setup_opensearch_dashboards.sh does: three index patterns with fixed ids
    (the dashboards reference them), the four ndjson files, and the default index."""
    d = dashboards(ctx)
    for pid, title in (ACCESS_PATTERN, AUDIT_PATTERN, ALL_PATTERN):
        d.post(f"/api/saved_objects/index-pattern/{pid}?overwrite=true",
               {"attributes": {"title": title, "timeFieldName": "@timestamp"}})
    counts = {}
    for name in NDJSON:
        body, hdrs = _multipart("file", name, bundled.text(f"opensearch/dashboards/{name}").encode())
        res = d.request("POST", "/api/saved_objects/_import?overwrite=true", raw=body, headers=hdrs)[1]
        if not isinstance(res, dict) or not res.get("success"):
            errors = (res or {}).get("errors") if isinstance(res, dict) else res
            raise InstallError(f"Dashboard import of {name} failed: {str(errors)[:400]}")
        counts[name] = int(res.get("successCount", 0))
    d.post("/api/opensearch-dashboards/settings", {"changes": {"defaultIndex": ACCESS_PATTERN[0]}})
    return counts


def count_dashboards(ctx) -> int:
    d = dashboards(ctx)
    res = d.get("/api/saved_objects/_find?type=dashboard&per_page=100") or {}
    return int(res.get("total", 0))


def index_counts(ctx, pattern: str) -> Dict[str, int]:
    rows = client(ctx).get(f"/_cat/indices/{pattern}?format=json&h=index,docs.count") or []
    return {r["index"]: int(r.get("docs.count") or 0) for r in rows}


def ism_policies(ctx) -> List[str]:
    res = client(ctx).get("/_plugins/_ism/policies") or {}
    return sorted(p["_id"] for p in res.get("policies", []))
