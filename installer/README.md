# Orcastra installers

Two automated installers, both Python 3 standard library only (3.8 or newer), each shipped as a
single-file zipapp:

| Installer | Deploys | User guide |
|---|---|---|
| `orcastra_mini_install` | Orcastra Mini on one Docker host (client-certificate sign-in) | [Mini Automated Install](../docs/mini/automated-install.md) |
| `orcastra_full_install` | Orcastra CMP Full on an LXD host: Authentik, Vault, OpenSearch and the CMP in four instances | [Automated Install](../docs/deployment/automated-install.md) |

## Install (end user)

```bash
# Mini, on the Docker host
curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get.sh | bash

# Full, on the LXD host
curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get-full.sh | sudo bash
```

Pass flags after `--`, for example `bash -s -- --answers /root/orcastra.env`. Questions are read
from `/dev/tty`, so both work through the pipe.

## Layout

```
get.sh, get-full.sh         bootstrap: ensure python3, fetch the zipapp, verify its sha256, run it
orcastra_core/              shared by both installers
  log.py proc.py prompt.py state.py fsutil.py netutil.py errors.py answers.py retry.py
orcastra_mini_install/
  cli.py context.py templates.py _blocks.py dockerutil.py
  phases/p01..p13, uninstall
orcastra_full_install/
  cli.py commands.py context.py config.py topology.py      answers, sizing, pins
  lxd.py remote.py                                         lxc CLI and in-instance execution
  vault_api.py authentik_api.py opensearch_api.py httpapi.py
  firewall.py cloudinit.py pki.py compose.py os_render.py cmp_render.py _blocks.py
  maintain.py status.py verify_infra.py verify_app.py
  assets/                   files the guides do not contain (release composes, dashboards, ...)
  phases/p01..p17, uninstall
tools/
  gen_blocks.py, check_templates.py          Mini: blocks from docs/mini/quick-start.md
  gen_full_blocks.py, check_full_assets.py   Full: blocks from docs/deployment/vm2..vm4
  build_pyz.py                               build both zipapps
tests/                      stdlib unittest suite (core, Mini, Full)
```

## Docs parity (single source of truth)

Both installers deploy config blocks taken from the manual guides, so the automated and the
manual path cannot drift apart.

- Mini: the heredocs in `docs/mini/quick-start.md` become `orcastra_mini_install/_blocks.py`
  (`tools/gen_blocks.py`), checked by `tools/check_templates.py`.
- Full: the heredocs, fenced blocks and OpenSearch `curl -X PUT` bodies in
  `docs/deployment/vm2..vm4` become `orcastra_full_install/_blocks.py`
  (`tools/gen_full_blocks.py`). `tools/check_full_assets.py` re-extracts them, checks that
  every installer-side edit still applies, and lists the files the installer owns outright
  with the reason.

After editing one of those guides, regenerate the module and commit both. CI
(`.github/workflows/installer.yml`) runs the unit tests and both parity checks on every change
under `installer/` or the guides.

## Develop

```bash
cd installer
python3 -m unittest discover -s tests -v
python3 tools/check_templates.py
python3 tools/check_full_assets.py
python3 -m orcastra_full_install --dry-run --non-interactive --admin-email a@example.com   # checks + plan, changes nothing
python3 -m orcastra_mini_install --dry-run --non-interactive -y --install-dir /tmp/mini --host 10.0.0.5
```

## Build and publish a release

```bash
python3 installer/tools/build_pyz.py      # -> installer/dist/orcastra-{mini,full}-install.pyz (+ .sha256)
```

Each installer has its own release tag (`installer-v<version>` for Mini,
`installer-full-v<version>` for Full). `get.sh` / `get-full.sh` point `PYZ_URL` at that tag and
carry the expected SHA-256 in `PINNED_SHA256`, so a replaced release asset is caught even if its
`.sha256` neighbour was replaced too. To cut a version: build, set `PYZ_URL` and
`PINNED_SHA256` in the bootstrap, commit, then `gh release create <tag> <pyz> <pyz>.sha256
--prerelease`. The public one-liners never change. `ORCASTRA_INSTALLER_URL`,
`ORCASTRA_INSTALLER_SHA_URL` and `ORCASTRA_INSTALLER_PYZ` point a bootstrap at a staging build
or a local file. `dist/` is gitignored.

A new CMP release for the Full installer needs its `docker-compose.prod.yml` under
`orcastra_full_install/assets/cmp/<version>/` and a row in `CMP_COMPOSE_SHA256`
(`topology.py`). The first row is what `latest` installs.
