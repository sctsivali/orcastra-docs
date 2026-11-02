#!/usr/bin/env python3
"""Fail when a tracked file holds an IPv4 address outside the allowed example ranges.

This repository is public. Guides, tests and installer code may show addresses only from the
ranges below; anything else is usually a real host that slipped in from a live system. Use a
placeholder such as <VM2_PRIVATE_IP>, or an address from one of these ranges, instead.

Usage: check_ip_literals.py [PATH ...]   (default: every file tracked by git)
"""
import ipaddress
import re
import subprocess
import sys
from pathlib import Path

ALLOWED_NETWORKS = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/32",        # bind to all interfaces
    "127.0.0.0/8",       # loopback, including Docker's embedded DNS 127.0.0.11
    "192.0.2.0/24",      # RFC 5737 TEST-NET-1
    "198.51.100.0/24",   # RFC 5737 TEST-NET-2
    "203.0.113.0/24",    # RFC 5737 TEST-NET-3
    "10.0.0.0/24",       # generic private examples (10.0.0.5 and so on)
    "10.77.0.0/24",      # Full installer default LXD bridge, also its tests
    "10.78.0.0/24",      # second bridge in the Full installer cluster tests
    "172.17.0.0/16",     # Docker default bridge
    "172.20.0.0/14",     # Full installer Docker address pool
    "192.168.1.0/24",    # home or office LAN examples
    "8.8.8.0/24",        # public DNS (8.8.8.8), also a "public address" case in tests
    "8.8.4.4/32",
    "1.1.1.1/32",
    "1.0.0.1/32",
    "9.9.9.9/32",
    "1.2.3.4/32",        # obviously fake public addresses used by installer tests
    "2.2.2.2/32",
)]

# Whole private ranges written out in CIDR form, for example in trusted proxy lists.
ALLOWED_CIDR_LITERALS = {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"}

# Four dotted numbers not inside a longer dotted run, so OIDs and versions such as 1.3.6.1.4.1
# do not match. A trailing full stop ends a sentence and is not part of the address.
IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(/\d{1,2})?(?!\d|\.\d)")


def violations(text):
    """Yield (line_number, literal) for every disallowed IPv4 literal in text."""
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in IPV4.finditer(line):
            literal = m.group(0)
            if literal in ALLOWED_CIDR_LITERALS:
                continue
            try:
                addr = ipaddress.ip_address(m.group(1))
            except ValueError:
                continue  # an octet above 255: not an address
            if not any(addr in net for net in ALLOWED_NETWORKS):
                yield lineno, literal


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True).stdout
    return [Path(p) for p in out.decode().split("\0") if p]


def main(argv):
    paths = [Path(p) for p in argv] or tracked_files()
    found = 0
    for path in paths:
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue  # binary
        for lineno, literal in violations(data.decode("utf-8", errors="replace")):
            print(f"::error file={path},line={lineno}::{literal} is outside the allowed example ranges")
            found += 1
    if found:
        print(f"{found} IPv4 literal(s) outside the allowed example ranges. Use a placeholder such as "
              "<VM2_PRIVATE_IP> or an address from scripts/check_ip_literals.py.", file=sys.stderr)
        return 1
    print(f"IPv4 literals OK ({len(paths)} file(s) checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
