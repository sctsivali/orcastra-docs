#!/usr/bin/env python3
"""Generate _blocks.py from docs/mini/quick-start.md.

The installer ships the deployment files (compose, nginx, vault.hcl, .env skeleton)
embedded as Python strings so it can run standalone with no git clone. To guarantee
those embedded copies never drift from the published manual guide, they are extracted
verbatim from the quick-start heredocs by this script. Parity is then enforced in CI
by check_templates.py.

Run from the orcastra-docs repo root:  python3 installer/tools/gen_blocks.py
"""
import json
import os
import re
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
QUICK_START = os.path.join(REPO_ROOT, "docs", "mini", "quick-start.md")
OUT = os.path.join(REPO_ROOT, "installer", "orcastra_mini_install", "_blocks.py")

# Heredoc path -> Python constant name.
WANT = {
    "docker-compose.yml": "COMPOSE",
    "config/nginx/mini.conf": "NGINX_CONF",
    "config/vault/vault.hcl": "VAULT_HCL",
    ".env": "ENV_DOC",
}


def extract_heredocs(text: str) -> dict:
    lines = text.splitlines(keepends=True)
    blocks: dict = {}
    i = 0
    while i < len(lines):
        m = re.match(r"cat > (\S+) <<'EOF'\s*$", lines[i].rstrip("\n"))
        if m:
            path = m.group(1)
            j = i + 1
            body = []
            while j < len(lines) and lines[j].rstrip("\n") != "EOF":
                body.append(lines[j])
                j += 1
            blocks[path] = "".join(body)
            i = j + 1
        else:
            i += 1
    return blocks


def build_source(blocks: dict) -> str:
    out = [
        "# AUTO-GENERATED from docs/mini/quick-start.md - do not edit by hand.",
        "# Regenerate: python3 installer/tools/gen_blocks.py",
        "# Parity is enforced by installer/tools/check_templates.py.",
        "",
    ]
    for path, name in WANT.items():
        # json.dumps yields a valid, fully escaped Python string literal.
        out.append(f"{name} = {json.dumps(blocks[path])}")
        out.append("")
    return "\n".join(out) + "\n"


def main() -> int:
    text = open(QUICK_START, encoding="utf-8").read()
    blocks = extract_heredocs(text)
    missing = set(WANT) - set(blocks)
    if missing:
        print(f"ERROR: missing heredocs in quick-start.md: {sorted(missing)}", file=sys.stderr)
        return 1
    comp = blocks["docker-compose.yml"]
    for ref in ("svlct/orcastra-dashboard-mini:backend-", "svlct/orcastra-dashboard-mini:frontend-"):
        if ref not in comp:
            print(f"ERROR: image ref {ref!r} not found in compose heredoc", file=sys.stderr)
            return 1
    source = build_source(blocks)
    open(OUT, "w", encoding="utf-8").write(source)
    print(f"Wrote {OUT}")
    for path, name in WANT.items():
        b = blocks[path]
        print(f"  {name:11s} <- {path:28s} {len(b):5d} bytes, {b.count(chr(10)):3d} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
