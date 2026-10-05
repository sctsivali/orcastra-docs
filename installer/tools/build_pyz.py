#!/usr/bin/env python3
"""Build the single-file zipapps the bootstrap scripts download and run.

  python3 installer/tools/build_pyz.py            # both
  python3 installer/tools/build_pyz.py mini|full  # one

Outputs installer/dist/orcastra-{mini,full}-install.pyz (+ .sha256). Each archive carries
its own package plus the shared orcastra_core package. Stdlib only.

The build is reproducible: entries are sorted and carry a fixed timestamp and mode, so the
same source gives the same sha256 as the one pinned in get.sh / get-full.sh.
"""
import hashlib
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALLER = os.path.dirname(HERE)
DIST = os.path.join(INSTALLER, "dist")

TARGETS = {
    "mini": ("orcastra_mini_install", "orcastra-mini-install.pyz"),
    "full": ("orcastra_full_install", "orcastra-full-install.pyz"),
}


def _entries(pkg: str):
    # our own __main__ (zipapp's generated one drops main()'s return value, so every
    # failure would exit 0)
    yield "__main__.py", f"import sys\nfrom {pkg}.cli import main\nsys.exit(main())\n".encode()
    for name in (pkg, "orcastra_core"):
        root = os.path.join(INSTALLER, name)
        for d, dirs, files in os.walk(root):
            dirs[:] = sorted(x for x in dirs if x != "__pycache__")
            for f in sorted(files):
                if f.endswith(".pyc"):
                    continue
                path = os.path.join(d, f)
                with open(path, "rb") as fh:
                    yield os.path.relpath(path, INSTALLER).replace(os.sep, "/"), fh.read()


def build(target: str) -> str:
    pkg, out_name = TARGETS[target]
    out = os.path.join(DIST, out_name)
    os.makedirs(DIST, exist_ok=True)
    with open(out, "wb") as raw:
        raw.write(b"#!/usr/bin/env python3\n")
        with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as zf:
            for arcname, data in sorted(_entries(pkg)):
                info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, data)
    os.chmod(out, 0o755)
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
