#!/usr/bin/env bash
# Orcastra CMP Full installer bootstrap (run on the LXD host).
#
#   curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get-full.sh | sudo bash
#
# Ensures python3 is present, fetches the single-file installer (a stdlib zipapp), verifies
# its checksum, and runs it. Pass installer flags after the URL, for example:
#   curl -fsSL .../get-full.sh | sudo bash -s -- --answers /root/orcastra.env
#
# The zipapp + checksum are served as GitHub Release assets (pinned, immutable). Override the
# source with ORCASTRA_INSTALLER_URL (and ORCASTRA_INSTALLER_SHA_URL), or run a local build with
# ORCASTRA_INSTALLER_PYZ=/path/to/orcastra-full-install.pyz.
set -euo pipefail

PYZ_URL="${ORCASTRA_INSTALLER_URL:-https://github.com/sctsivali/orcastra-docs/releases/download/installer-full-v1.0.0-RC1/orcastra-full-install.pyz}"
SHA_URL="${ORCASTRA_INSTALLER_SHA_URL:-${PYZ_URL}.sha256}"
LOCAL_PYZ="${ORCASTRA_INSTALLER_PYZ:-}"   # skip download, use this local zipapp
# Expected digest of the default PYZ_URL, pinned here so a tampered release asset is caught
# even when its .sha256 neighbour was replaced too. Empty when PYZ_URL is overridden.
PINNED_SHA256="15d8338e693ac3e3ec915171157c95c53b632f6220557d0fc56400cd4f16bb5f"
if [ -n "${ORCASTRA_INSTALLER_URL:-}" ]; then PINNED_SHA256=""; fi   # an override is checked against its .sha256

say() { printf '  %s\n' "$*"; }
die() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

need_sudo() {
  if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 || die "root privileges required (no sudo found)."
    echo "sudo"
  fi
}

ensure_python() {
  command -v python3 >/dev/null 2>&1 && return 0
  say "python3 is missing, installing it ..."
  if [ -r /etc/os-release ]; then . /etc/os-release; fi
  case "${ID:-}${ID_LIKE:-}" in
    *debian*|*ubuntu*)
      local S; S="$(need_sudo || true)"
      $S apt-get update -y && $S apt-get install -y python3 ;;
    *) die "Install python3 manually, then re-run." ;;
  esac
}

fetch() {  # fetch URL -> stdout
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$1"
  elif command -v wget >/dev/null 2>&1; then wget -qO- "$1"
  else die "need curl or wget to download the installer."; fi
}

main() {
  ensure_python

  local pyz
  if [ -n "$LOCAL_PYZ" ]; then
    [ -r "$LOCAL_PYZ" ] || die "ORCASTRA_INSTALLER_PYZ not readable: $LOCAL_PYZ"
    pyz="$LOCAL_PYZ"
    say "Using local installer: $pyz"
  else
    local tmp; tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    pyz="$tmp/orcastra-full-install.pyz"
    say "Downloading installer ..."
    fetch "$PYZ_URL" > "$pyz" || die "download failed: $PYZ_URL"
    local expected actual
    if [ -z "${ORCASTRA_INSTALLER_URL:-}" ] && [ -z "$PINNED_SHA256" ]; then
      die "this bootstrap has no pinned checksum for its default download (release error)."
    fi
    expected="$PINNED_SHA256"
    if [ -z "$expected" ]; then
      expected="$(fetch "$SHA_URL" 2>/dev/null | awk '{print $1}')" || expected=""
    fi
    if [ -z "$expected" ]; then
      [ "${ORCASTRA_INSTALLER_INSECURE:-}" = "1" ] \
        || die "no checksum available for $PYZ_URL (set ORCASTRA_INSTALLER_INSECURE=1 to run unverified)."
      say "Warning: running WITHOUT checksum verification (ORCASTRA_INSTALLER_INSECURE=1)."
    else
      actual="$(sha256sum "$pyz" | awk '{print $1}')"
      [ "$expected" = "$actual" ] || die "checksum mismatch (expected $expected, got $actual)."
      say "Checksum verified."
    fi
  fi

  # Questions are read from the terminal (/dev/tty) by the installer itself, so this works
  # when the script arrives through `curl | bash`.
  local rc=0
  if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 || die "root privileges required (no sudo found)."
    sudo -E python3 "$pyz" "$@" || rc=$?
  else
    python3 "$pyz" "$@" || rc=$?
  fi
  exit "$rc"
}

main "$@"
