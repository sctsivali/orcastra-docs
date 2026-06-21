#!/usr/bin/env python3
"""CI parity check: the installer's embedded templates must equal the published heredocs in
docs/mini/quick-start.md. Fails (exit 1) on any drift so the docs and installer can never
diverge silently. Run: python3 installer/tools/check_templates.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALLER = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(INSTALLER)
sys.path.insert(0, INSTALLER)

from orcastra_mini_install import templates as T          # noqa: E402
from orcastra_mini_install import _blocks                 # noqa: E402

sys.path.insert(0, HERE)
from gen_blocks import extract_heredocs, QUICK_START, WANT  # noqa: E402


def main() -> int:
    docs = extract_heredocs(open(QUICK_START, encoding="utf-8").read())
    problems = []

    for path in WANT:
        if path not in docs:
            problems.append(f"quick-start.md no longer contains heredoc for {path}")

    checks = {
        "docker-compose.yml": (_blocks.COMPOSE, "_blocks.COMPOSE"),
        "config/nginx/mini.conf": (_blocks.NGINX_CONF, "_blocks.NGINX_CONF"),
        "config/vault/vault.hcl": (_blocks.VAULT_HCL, "_blocks.VAULT_HCL"),
        ".env": (_blocks.ENV_DOC, "_blocks.ENV_DOC"),
    }
    for path, (embedded, name) in checks.items():
        if path in docs and docs[path] != embedded:
            problems.append(f"{name} drifted from quick-start.md heredoc for {path} "
                            "(run: python3 installer/tools/gen_blocks.py)")

    # Rendered outputs must match the verbatim blocks at the default tag / key set.
    if T.render_compose() != _blocks.COMPOSE:
        problems.append("render_compose() at the default tag does not equal the verbatim compose")
    if T.nginx_conf() != _blocks.NGINX_CONF:
        problems.append("nginx_conf() does not equal the verbatim nginx config")
    if T.vault_hcl() != _blocks.VAULT_HCL:
        problems.append("vault_hcl() does not equal the verbatim vault.hcl")

    # render_env must emit exactly the documented key set (no silent additions/removals).
    filled = {k: "x" for k in T.FILLABLE_ENV_KEYS}
    rendered = T.render_env(filled)
    gen_keys = [l.split("=", 1)[0] for l in rendered.splitlines()
                if l and not l.startswith("#") and "=" in l]
    if gen_keys != T.env_doc_keys():
        problems.append(f"render_env key set drifted from docs .env:\n  gen:  {gen_keys}\n  docs: {T.env_doc_keys()}")

    if problems:
        print("TEMPLATE PARITY FAILED:")
        for p in problems:
            print("  - " + p)
        return 1
    print("Template parity OK (compose, nginx, vault.hcl, .env all match quick-start.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
