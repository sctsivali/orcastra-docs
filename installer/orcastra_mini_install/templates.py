"""Deployment-file rendering for the mini installer.

The raw bodies live in `_blocks.py`, extracted verbatim from docs/mini/quick-start.md so
the installer's output is identical to the published manual guide (parity enforced by
tools/check_templates.py). This module only substitutes the image tag and fills the .env
values; it never changes structure, ordering, or comments.
"""
from . import _blocks

DEFAULT_IMAGE_TAG = "1.0.0"

# The compose names its images by repository and APP_VERSION from .env; a run that brings its own
# images (--image-backend/--image-frontend) replaces these two lines.
_BACKEND_IMAGE_LINE = "    image: ${MINI_IMAGE_REPO:-svlct/orcastra-dashboard-mini}:backend-${APP_VERSION:-1.0.0}"
_FRONTEND_IMAGE_LINE = "    image: ${MINI_IMAGE_REPO:-svlct/orcastra-dashboard-mini}:frontend-${APP_VERSION:-1.0.0}"

# Keys whose value the installer fills in .env. Every other line (comments, fixed
# defaults like AUTH_MODE) is kept exactly as the docs publish it.
FILLABLE_ENV_KEYS = (
    "CONTAINER_PREFIX",
    "APP_VERSION",
    "HTTPS_PORT",
    "HTTPS_BIND",
    "UPLOADS_DIR",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "DATABASE_URL",
    "SECRET_KEY",
    "LOCAL_JWT_SECRET",
    "NEXTAUTH_SECRET",
    "AUTH_PROXY_SECRET",
    "BOOTSTRAP_ADMIN_TOKEN",
    "LOCAL_JWT_TTL_SECONDS",
    "CLIENT_CERT_TTL_DAYS",
    "VAULT_TOKEN",
    "NEXTAUTH_URL",
    "NEXT_PUBLIC_API_URL",
    "CORS_ORIGINS",
)


def render_compose(image_tag: str = DEFAULT_IMAGE_TAG, backend_image: str = None,
                   frontend_image: str = None) -> str:
    """The pull-based compose. The image tag comes from APP_VERSION in .env; image_tag only moves
    the default, and explicit images replace the two image lines."""
    out = _blocks.COMPOSE
    if _BACKEND_IMAGE_LINE not in out or _FRONTEND_IMAGE_LINE not in out:
        raise KeyError("the compose block no longer names its images as expected (docs drift?)")
    out = out.replace(_BACKEND_IMAGE_LINE, f"    image: {backend_image}" if backend_image
                      else _BACKEND_IMAGE_LINE.replace(":-1.0.0}", f":-{image_tag}}}"))
    out = out.replace(_FRONTEND_IMAGE_LINE, f"    image: {frontend_image}" if frontend_image
                      else _FRONTEND_IMAGE_LINE.replace(":-1.0.0}", f":-{image_tag}}}"))
    return out


def env_skeleton() -> str:
    """The published .env block, placeholders included."""
    return _blocks.ENV_DOC


def nginx_conf() -> str:
    return _blocks.NGINX_CONF


def vault_hcl() -> str:
    return _blocks.VAULT_HCL


def env_doc_keys() -> list:
    """Ordered list of env keys present in the published quick-start .env block."""
    return [k for k, _ in _iter_env_lines(_blocks.ENV_DOC) if k is not None]


def _iter_env_lines(text: str):
    """Yield (key, line) for each line. key is None for comments/blanks."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            yield None, line
        else:
            key = line.split("=", 1)[0].strip()
            yield key, line


def render_env(values: dict) -> str:
    """Fill the docs .env skeleton with real values.

    Structure, ordering, and comments come straight from the published heredoc; only the
    right-hand side of `KEY=` lines for keys in `values` is replaced. This guarantees the
    generated file has exactly the documented key set (no silent additions/removals).
    """
    unknown = set(values) - set(FILLABLE_ENV_KEYS)
    if unknown:
        raise KeyError(f"refusing to set non-fillable env keys: {sorted(unknown)}")
    out_lines = []
    seen = set()
    for key, line in _iter_env_lines(_blocks.ENV_DOC):
        if key is not None and key in values:
            out_lines.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out_lines.append(line)
    missing = [k for k in values if k not in seen]
    if missing:
        raise KeyError(f"env keys not found in skeleton (docs drift?): {missing}")
    text = "\n".join(out_lines)
    if not text.endswith("\n"):
        text += "\n"
    return text


def set_env_value(text: str, key: str, value: str) -> str:
    """Return `text` with the single `KEY=...` line rewritten. Used for in-place edits
    (e.g. writing VAULT_TOKEN after Vault setup, blanking BOOTSTRAP_ADMIN_TOKEN)."""
    out_lines = []
    found = False
    for k, line in _iter_env_lines(text):
        if k == key:
            out_lines.append(f"{key}={value}")
            found = True
        else:
            out_lines.append(line)
    if not found:
        raise KeyError(f"env key not present: {key}")
    result = "\n".join(out_lines)
    if text.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def get_env_value(text: str, key: str):
    """Read a single env value back (returns None if absent)."""
    for k, line in _iter_env_lines(text):
        if k == key:
            return line.split("=", 1)[1].strip()
    return None
