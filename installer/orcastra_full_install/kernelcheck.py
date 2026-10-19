"""Host kernel check for CVE-2026-99999 ("ipv4: validate checksum_start before completing
checksum"). A KVM guest can hand the host bad checksum metadata through the virtio-net/TAP path,
and skb_checksum_help() then corrupts the host's IPv4 header. Vendors list no workaround, so the
only fix is booting a patched kernel.

The check proves the fix where the host can show it (the running kernel's package changelog, a
Debian `uname -v` package version, an upstream sublevel) and says plainly where it cannot. It
never returns FAIL: when this was written Ubuntu had published no fixed kernel, so a FAIL would
block every install on the supported OS.

note: orcastra-dashboard carries the same advisory data for the cluster card badge
(backend/app/services/kernel_advisory_data.py). Update both when a vendor publishes a fix.
"""
from __future__ import annotations

import glob
import gzip
import os
import re
from typing import Dict, List, NamedTuple, Optional, Tuple

CVE = "CVE-2026-99999"
DATA_AS_OF = "2026-10-19"
REFERENCE = "https://lists.openwall.net/linux-cve-announce/2026/10/15/2"
DOCS = "https://docs.orcastra.io/en/latest/operations/security-advisories/"

# note: from the linux-cve-announce entry above. Introduced in 2.6.27; the first fixed
# sublevel on each maintained stable branch, and the first mainline release candidate.
INTRODUCED = (2, 6, 27)
STABLE_FIXES = {
    (5, 10): 272,
    (5, 15): 223,
    (6, 1): 190,
    (6, 6): 159,
    (6, 12): 113,
    (6, 18): 57,
    (7, 2): 11,
}
MAINLINE_FIXED = (7, 3)
MAINLINE_FIXED_RC = 7

PASS = "PASS"
WARN = "WARN"

_RELEASE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:-rc(\d+))?(.*)$")
_DEBIAN_PACKAGE = re.compile(r"\bDebian (\d+)\.(\d+)\.(\d+)-(\S+)")
_RHEL_LOCAL = re.compile(r"\.el\d")


class Release(NamedTuple):
    major: int
    minor: int
    sublevel: int
    rc: Optional[int]
    local: str

    @property
    def branch(self) -> Tuple[int, int]:
        return (self.major, self.minor)

    @property
    def triple(self) -> Tuple[int, int, int]:
        return (self.major, self.minor, self.sublevel)


def parse_release(raw: Optional[str]) -> Optional[Release]:
    m = _RELEASE.match((raw or "").strip())
    if not m:
        return None
    major, minor, sub, rc, local = m.groups()
    return Release(int(major), int(minor), int(sub or 0), int(rc) if rc else None, local or "")


def _mainline_has_fix(rel: Release) -> bool:
    if rel.branch != MAINLINE_FIXED:
        return rel.branch > MAINLINE_FIXED
    return rel.rc is None or rel.rc >= MAINLINE_FIXED_RC


def _upstream(rel: Release, label: str) -> Tuple[str, str]:
    """Judge an upstream sublevel. Only called where the sublevel tracks upstream stable."""
    if _mainline_has_fix(rel):
        return PASS, f"{label} contains the upstream fix"
    fixed = STABLE_FIXES.get(rel.branch)
    if fixed is None:
        return WARN, (f"exposed: {label} is on a {rel.major}.{rel.minor} branch that "
                      f"received no upstream fix")
    if rel.sublevel >= fixed:
        return PASS, f"{label} is at or above {rel.major}.{rel.minor}.{fixed}"
    return WARN, f"exposed: {label} is below {rel.major}.{rel.minor}.{fixed}"


def _debian_family(osr: Dict[str, str]) -> bool:
    ids = [osr.get("ID", "")] + osr.get("ID_LIKE", "").split()
    return any(i in ("debian", "ubuntu") for i in ids)


def _rhel_family(osr: Dict[str, str], rel: Release) -> bool:
    ids = [osr.get("ID", "")] + osr.get("ID_LIKE", "").split()
    return bool(_RHEL_LOCAL.search(rel.local)) or any(
        i in ("rhel", "centos", "rocky", "almalinux", "ol") for i in ids)


# important: on Ubuntu the linux-image-<release> changelog is the linux-signed one, which only
# names the main version. The CVE list lives in linux-modules-<release> (the kernel source
# changelog), so both are read. Debian's own changelog is checked through `uname -v` too.
_CHANGELOG_PACKAGES = ("linux-modules", "linux-image", "linux-image-unsigned")


def changelog_paths(release: str, doc_root: str) -> List[str]:
    return [os.path.join(doc_root, f"{pkg}-{release}", "changelog.Debian.gz")
            for pkg in _CHANGELOG_PACKAGES]


def release_has_fix(release: str, doc_root: str) -> bool:
    return any(changelog_mentions(p) for p in changelog_paths(release, doc_root))


def changelog_mentions(path: str, needle: str = CVE) -> bool:
    """True when the gzip changelog names the CVE. Streams, so a large changelog stays cheap."""
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if needle in line:
                    return True
    except (OSError, EOFError):
        return False
    return False


def _sort_key(release: str) -> Tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", release))


def installed_releases(doc_root: str) -> List[str]:
    """Kernel releases that left a changelog under doc_root, from the package directory names."""
    found = set()
    for pkg in _CHANGELOG_PACKAGES:
        prefix = f"{pkg}-"
        for path in glob.glob(os.path.join(doc_root, prefix + "*", "changelog.Debian.gz")):
            release = os.path.basename(os.path.dirname(path))[len(prefix):]
            if parse_release(release) is not None:   # skips meta packages such as linux-image-virtual
                found.add(release)
    return sorted(found, key=_sort_key, reverse=True)


def fixed_installed_kernels(running: str, doc_root: str) -> List[str]:
    """Other installed kernels whose changelog names the fix, newest first."""
    return [r for r in installed_releases(doc_root)
            if r != running and release_has_fix(r, doc_root)]


def assess(release: str, version: str, osr: Dict[str, str],
           doc_root: str = "/usr/share/doc") -> Tuple[str, str]:
    """(PASS|WARN, one-line detail) for the running kernel. Inputs are injectable for tests:
    `release` is `uname -r`, `version` is `uname -v`, `osr` is /etc/os-release as a dict."""
    rel = parse_release(release)
    if rel is None:
        return WARN, f"could not verify: kernel version {release!r} not understood"
    if rel.triple < INTRODUCED:
        return PASS, f"{release} predates the bug"
    if _mainline_has_fix(rel):
        return PASS, f"{release} contains the upstream fix"

    if _debian_family(osr):
        if release_has_fix(release, doc_root):
            return PASS, f"{release}: package changelog lists the fix"
        m = _DEBIAN_PACKAGE.search(version or "")
        if m:
            pkg = Release(int(m.group(1)), int(m.group(2)), int(m.group(3)), None, "")
            return _upstream(pkg, f"Debian package {'.'.join(m.groups()[:3])}-{m.group(4)}")
        if rel.sublevel > 0:
            return _upstream(rel, release)
        newer = fixed_installed_kernels(release, doc_root)
        if newer:
            return WARN, f"running {release} lacks the fix; reboot into {newer[0]}"
        return WARN, f"exposed or unverified: no fix found for {release} (as of {DATA_AS_OF})"

    if _rhel_family(osr, rel):
        return WARN, f"could not verify vendor kernel {release}: check the rpm changelog"
    if rel.sublevel > 0:
        return _upstream(rel, release)
    return WARN, f"could not verify {release}: vendor kernel without an upstream sublevel"


def check_host(osr: Dict[str, str], vm_capable: bool) -> Tuple[str, str]:
    """assess() for this machine. A KVM host gets the reason the check matters on the row."""
    uts = os.uname()
    status, detail = assess(uts.release, uts.version, osr)
    if status != PASS and vm_capable:
        detail += " (VM guests can reach this path)"
    return status, detail


def check_command(osr: Dict[str, str]) -> str:
    if _debian_family(osr):
        return (f"zgrep -s -m1 {CVE} /usr/share/doc/linux-{{modules,image}}-$(uname -r)/"
                f"changelog.Debian.gz; uname -v")
    return f"rpm -q --changelog kernel-core-$(uname -r) | grep -m1 {CVE}; uname -r"
