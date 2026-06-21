#!/usr/bin/env python3
"""Build the single-file zipapp the bootstrap (get.sh) downloads and runs.

Output: installer/dist/orcastra-mini-install.pyz (+ .sha256). Stdlib only (zipapp).
Run from the repo root: python3 installer/tools/build_pyz.py
"""
import hashlib
import os
import shutil
import tempfile
import zipapp

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALLER = os.path.dirname(HERE)
PKG = os.path.join(INSTALLER, "orcastra_mini_install")
DIST = os.path.join(INSTALLER, "dist")
OUT = os.path.join(DIST, "orcastra-mini-install.pyz")


def main():
    os.makedirs(DIST, exist_ok=True)
    build = tempfile.mkdtemp(prefix="orcastra-pyz-")
    try:
        shutil.copytree(PKG, os.path.join(build, "orcastra_mini_install"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        zipapp.create_archive(
            build, target=OUT, interpreter="/usr/bin/env python3",
            main="orcastra_mini_install.cli:main")
    finally:
        shutil.rmtree(build, ignore_errors=True)
    digest = hashlib.sha256(open(OUT, "rb").read()).hexdigest()
    with open(OUT + ".sha256", "w", encoding="utf-8") as fh:
        fh.write(f"{digest}  {os.path.basename(OUT)}\n")
    size = os.path.getsize(OUT)
    print(f"Built {OUT} ({size} bytes)")
    print(f"sha256 {digest}")


if __name__ == "__main__":
    main()
