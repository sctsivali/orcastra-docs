#!/usr/bin/env bash
# Orcastra Mini installer bootstrap.
#
#   curl -fsSL https://docs.orcastra.io/installer/get.sh | bash
#
# Ensures python3 is present, fetches the single-file installer (a stdlib zipapp), verifies
# its checksum, and runs it. Pass installer flags after the URL, e.g.:
#   curl -fsSL .../get.sh | bash -s -- --host 10.0.0.5 --quick
set -euo pipefail

PYZ_URL="${ORCASTRA_INSTALLER_URL:-https://docs.orcastra.io/installer/orcastra-mini-install.pyz}"
SHA_URL="${ORCASTRA_INSTALLER_SHA_URL:-${PYZ_URL}.sha256}"
LOCAL_PYZ="${ORCASTRA_INSTALLER_PYZ:-}"   # skip download, use this local zipapp

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
  say "python3 is missing; installing it ..."
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
    pyz="$tmp/orcastra-mini-install.pyz"
    say "Downloading installer ..."
    fetch "$PYZ_URL" > "$pyz" || die "download failed: $PYZ_URL"
    if expected="$(fetch "$SHA_URL" 2>/dev/null | awk '{print $1}')" && [ -n "$expected" ]; then
      actual="$(sha256sum "$pyz" | awk '{print $1}')"
      [ "$expected" = "$actual" ] || die "checksum mismatch (expected $expected, got $actual)."
      say "Checksum verified."
    else
      say "Warning: no checksum available; proceeding without verification."
    fi
  fi

  exec python3 "$pyz" "$@"
}

main "$@"
