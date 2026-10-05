"""Minimal JSON-over-HTTP client (urllib) used for the Vault, Authentik and OpenSearch
admin APIs. Every call has a timeout, and errors carry the status and a short body."""
from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from orcastra_core.errors import InstallError


class HttpError(InstallError):
    def __init__(self, status: int, body: str, url: str) -> None:
        super().__init__(f"HTTP {status} from {url}: {body[:300]}",
                         remediation="Check that the service named in the URL is up, then re-run; "
                                     "the installer resumes where it stopped.")
        self.status = status
        self.body = body
        self.url = url


class Client:
    def __init__(self, base: str, *, headers: Optional[Dict[str, str]] = None,
                 basic: Optional[Tuple[str, str]] = None, cafile: Optional[str] = None,
                 timeout: float = 20.0) -> None:
        self.base = base.rstrip("/")
        self.headers = dict(headers or {})
        if basic:
            tok = base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode()
            self.headers["Authorization"] = f"Basic {tok}"
        self.timeout = timeout
        self.ctx = None
        if self.base.startswith("https"):
            self.ctx = ssl.create_default_context(cafile=cafile)  # verified, always
        # admin traffic to the instances must never go through a proxy from the environment
        handlers = [urllib.request.ProxyHandler({})]
        if self.ctx is not None:
            handlers.append(urllib.request.HTTPSHandler(context=self.ctx))
        self._opener = urllib.request.build_opener(*handlers)

    def request(self, method: str, path: str, body: Any = None, *,
                headers: Optional[Dict[str, str]] = None, raw: Optional[bytes] = None,
                ok: Tuple[int, ...] = (200, 201, 202, 204)) -> Tuple[int, Any]:
        url = self.base + path
        data = raw
        hdrs = dict(self.headers)
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                status, text = resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, text = exc.code, exc.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            raise HttpError(0, str(exc), url) from None
        if status not in ok:
            raise HttpError(status, text, url)
        if not text.strip():
            return status, None
        try:
            return status, json.loads(text)
        except ValueError:
            return status, text

    def get(self, path: str, **kw) -> Any:
        return self.request("GET", path, **kw)[1]

    def post(self, path: str, body: Any = None, **kw) -> Any:
        return self.request("POST", path, body, **kw)[1]

    def put(self, path: str, body: Any = None, **kw) -> Any:
        return self.request("PUT", path, body, **kw)[1]

    def patch(self, path: str, body: Any = None, **kw) -> Any:
        return self.request("PATCH", path, body, **kw)[1]

    def delete(self, path: str, **kw) -> Any:
        return self.request("DELETE", path, **kw)[1]
