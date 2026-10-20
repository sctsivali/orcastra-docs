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

from . import __version__

# note: an explicit agent, because Cloudflare's Browser Integrity Check answers 403 (error 1010)
# to urllib's default "Python-urllib/3.x" on a public hostname
USER_AGENT = f"orcastra-full/{__version__}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hand a redirect back to the caller (as an HTTPError) instead of following it."""

    def redirect_request(self, *a, **kw):  # noqa: ANN002 - urllib signature
        return None


def location(url: str, timeout: float = 20.0) -> Tuple[int, str]:
    """(status, Location header) of a GET that is not followed."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    opener.addheaders = [("User-Agent", USER_AGENT)]
    try:
        with opener.open(url, timeout=timeout) as resp:
            return resp.status, ""
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location", "")
    except (urllib.error.URLError, OSError) as exc:
        raise HttpError(0, str(exc), url) from None


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
                 cert: Optional[Tuple[str, str]] = None, timeout: float = 20.0) -> None:
        self.base = base.rstrip("/")
        self.headers = {"User-Agent": USER_AGENT, **(headers or {})}
        if basic:
            tok = base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode()
            self.headers["Authorization"] = f"Basic {tok}"
        self.timeout = timeout
        self.ctx = None
        if self.base.startswith("https"):
            self.ctx = ssl.create_default_context(cafile=cafile)  # verified, always
            if cert:
                self.ctx.load_cert_chain(cert[0], cert[1])
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
