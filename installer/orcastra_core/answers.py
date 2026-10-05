"""Answer sources for unattended runs: a flat KEY=value file and ORCASTRA_* environment
variables. Precedence everywhere is CLI flag > answer file > environment > default."""
from __future__ import annotations

import os
from typing import Dict, Mapping, Optional

TRUTHY = {"1", "true", "yes", "on", "y"}
FALSY = {"0", "false", "no", "off", "n", ""}


def parse_answer_file(path: str) -> Dict[str, str]:
    """Read KEY=value lines. Blank lines and # comments are ignored, surrounding quotes on a
    value are stripped so a file sourced by a shell reads the same way here."""
    out: Dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, val = line.split("=", 1)
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                val = val[1:-1]
            out[k.strip().upper()] = val
    return out


def env_answers(prefix: str = "ORCASTRA_",
                environ: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """ORCASTRA_HOST=1.2.3.4 becomes {"HOST": "1.2.3.4"}. Installer-internal variables used
    by the bootstrap script (ORCASTRA_INSTALLER_*) are not answers and are skipped."""
    env = os.environ if environ is None else environ
    out: Dict[str, str] = {}
    for k, v in env.items():
        if k.startswith(prefix) and not k.startswith(prefix + "INSTALLER_"):
            out[k[len(prefix):].upper()] = v
    return out


def combined_answers(file_path: Optional[str], *, prefix: str = "ORCASTRA_",
                     environ: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Environment first, then the answer file on top (the file is more explicit)."""
    merged = env_answers(prefix, environ)
    if file_path:
        merged.update(parse_answer_file(file_path))
    return merged


def as_bool(value: str) -> bool:
    v = value.strip().lower()
    if v in TRUTHY:
        return True
    if v in FALSY:
        return False
    raise ValueError(f"not a boolean: {value!r}")
