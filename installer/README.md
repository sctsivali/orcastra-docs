# Orcastra Mini installer

Automated, end-to-end installer for the Orcastra Mini single-instance deployment. It runs the
manual [Quick Start](../docs/mini/quick-start.md) sequence for the operator: preflight checks,
optional Docker install, secret and certificate generation, config files, image pull, Vault
init/unseal/PKI, the first-admin bootstrap, and a health verification - idempotent on re-run.

Python 3 standard library only. No third-party packages.

## Install (end user)

```bash
curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get.sh | bash
```

Pass flags after `--`, e.g. `bash -s -- --host 10.0.0.5 --quick`. Full guide:
[Automated Install](../docs/mini/automated-install.md).

## Layout

```
get.sh                      bootstrap: ensure python3, fetch+verify the zipapp, exec it
orcastra_mini_install/
  cli.py                    flags, answer-file merge, phase dispatch
  context.py log.py proc.py state.py prompt.py errors.py    shared infrastructure
  netutil.py dockerutil.py fsutil.py                        helpers
  templates.py _blocks.py   deployment-file rendering (see "Templates" below)
  phases/p01..p13, uninstall the install steps
tools/
  gen_blocks.py             regenerate _blocks.py from the published heredocs
  check_templates.py        CI parity check (templates vs quick-start.md)
  build_pyz.py              build the single-file zipapp for release
tests/                      stdlib unittest suite
```

## Phases

`preflight -> docker -> login -> wizard -> secrets -> tls -> write -> compose -> health ->
vault -> bootstrap -> verify -> summary`. Each is `run(ctx)` and records `done` in
`install-state.json`, so a re-run skips finished steps. The backend cannot be healthy before
`vault` (it needs `VAULT_TOKEN`), so `health` gates only the data tier and `vault` waits on the
backend after writing the token.

## Templates (single source of truth)

The deployment files the installer writes (`docker-compose.yml`, `config/nginx/mini.conf`,
`config/vault/vault.hcl`, and the `.env` skeleton) are extracted verbatim from the published
heredocs in `docs/mini/quick-start.md` into `orcastra_mini_install/_blocks.py`. This guarantees
the installer's output matches the manual guide.

- Regenerate after editing the quick-start: `python3 installer/tools/gen_blocks.py`
- Parity is enforced in CI: `python3 installer/tools/check_templates.py`

`templates.py` only substitutes the image tag and fills `.env` values; it never changes
structure, ordering, or the documented key set.

## Develop

```bash
cd installer
python3 -m unittest discover -s tests -v      # unit tests
python3 tools/check_templates.py              # template parity
python3 -m orcastra_mini_install --dry-run --non-interactive -y \
  --install-dir /tmp/mini --host 10.0.0.5     # render everything, change nothing
python3 -m orcastra_mini_install ... --stop-after write   # generate files only
```

## Build and publish a release

The zipapp and its checksum are distributed as GitHub Release assets (immutable, versioned). The
one-liner serves `get.sh` from `raw.githubusercontent.com` on `main`, and `get.sh` pins the
`.pyz` to a release tag and verifies its SHA-256 before running it.

```bash
# 1. Build the artifact
python3 installer/tools/build_pyz.py          # -> installer/dist/orcastra-mini-install.pyz (+ .sha256)

# 2. Cut/refresh the release for the tag get.sh points at (see PYZ_URL in get.sh)
gh release create installer-v1.0.0-RC1 \
  installer/dist/orcastra-mini-install.pyz \
  installer/dist/orcastra-mini-install.pyz.sha256 \
  installer/get.sh \
  --prerelease --title "Orcastra Mini installer 1.0.0-RC1" \
  --notes "Automated installer zipapp + checksum."
```

For a new installer version, bump the tag in `get.sh` (`PYZ_URL`) and cut a release on that tag;
the public one-liner never changes. `get.sh` honors `ORCASTRA_INSTALLER_URL` /
`ORCASTRA_INSTALLER_SHA_URL` / `ORCASTRA_INSTALLER_PYZ` for staging or local use. The `dist/`
directory is a build artifact and is gitignored.
