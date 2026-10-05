#!/usr/bin/env python3
"""Build the single-file zipapps the bootstrap scripts download and run.

  python3 installer/tools/build_pyz.py            # both
  python3 installer/tools/build_pyz.py mini|full  # one

Outputs installer/dist/orcastra-{mini,full}-install.pyz (+ .sha256). Each archive carries
its own package plus the shared orcastra_core package. Stdlib only (zipapp).
"""
import hashlib
import os
import shutil
import sys
import tempfile
import zipapp

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALLER = os.path.dirname(HERE)
DIST = os.path.join(INSTALLER, "dist")

TARGETS = {
    "mini": ("orcastra_mini_install", "orcastra-mini-install.pyz"),
    "full": ("orcastra_full_install", "orcastra-full-install.pyz"),
}


def build(target: str) -> str:
    pkg, out_name = TARGETS[target]
    out = os.path.join(DIST, out_name)
    os.makedirs(DIST, exist_ok=True)
    build_dir = tempfile.mkdtemp(prefix="orcastra-pyz-")
    try:
        for name in (pkg, "orcastra_core"):
            shutil.copytree(os.path.join(INSTALLER, name), os.path.join(build_dir, name),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # our own __main__ (zipapp's generated one drops main()'s return value, so every
        # failure would exit 0)
        with open(os.path.join(build_dir, "__main__.py"), "w", encoding="utf-8") as fh:
            fh.write(f"import sys\nfrom {pkg}.cli import main\nsys.exit(main())\n")
        zipapp.create_archive(build_dir, target=out, interpreter="/usr/bin/env python3")
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)
    with open(out, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    with open(out + ".sha256", "w", encoding="utf-8") as fh:
        fh.write(f"{digest}  {os.path.basename(out)}\n")
    print(f"Built {out} ({os.path.getsize(out)} bytes)")
    print(f"sha256 {digest}")
    return out


def main() -> None:
    wanted = sys.argv[1:] or list(TARGETS)
    for t in wanted:
        if t not in TARGETS:
            sys.exit(f"unknown target {t!r}, expected one of {', '.join(TARGETS)}")
        build(t)


if __name__ == "__main__":
    main()
