# Security Advisories

Advisories here cover problems outside the Orcastra code that still decide whether a
deployment is safe to run: host kernels, hypervisors and the platforms Orcastra drives. Each
entry says who is affected, how to tell, and what fixes it. Status lines carry the date they
were checked, because vendor positions change after publication.

---

## CVE-2026-99999: guest-to-host flaw in the host kernel's IPv4 checksum path

*Checked 2026-10-19.*

A packet that reaches the host's IPv4 stack with bad checksum metadata can make
`skb_checksum_help()` corrupt the packet's network header after it was validated. A KVM guest
can send such a packet through the virtio-net/TAP path, so code running inside a virtual
machine can disclose host memory or crash the host. Researchers also showed guest-to-host code
execution, in lab conditions only. The upstream fix is titled
"ipv4: validate checksum_start before completing checksum".

The bug is in the Linux kernel of the LXD host. Orcastra itself is not vulnerable, and nothing
in an Orcastra release changes your exposure. It still matters to every Orcastra operator:

- the automated installer creates virtual machines by default, each with a bridged NIC, which
  LXD runs as virtio-net over a TAP device on the host;
- tenants get a root shell inside their instances through the console and terminal, and root
  in the guest is the starting point this attack needs.

### Severity

Three scores are published and they disagree on one metric, the privileges an attacker needs
inside the guest.

| Source | Score | Vector or rating |
|---|---|---|
| Linux kernel CNA | 9.3 Critical | `CVSS:3.1/AV:L/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H` |
| Red Hat | 8.8 Important | same vector with `PR:L` |
| Ubuntu | priority Medium | shown alongside the CNA's 9.3 |

### Affected and fixed kernels

The flaw was introduced in 2.6.27 (commit `f43798c27684`). Upstream fixed it on every
maintained stable branch:

| Branch | First fixed release |
|---|---|
| 5.10 | 5.10.272 |
| 5.15 | 5.15.223 |
| 6.1 | 6.1.190 |
| 6.6 | 6.6.159 |
| 6.12 | 6.12.113 |
| 6.18 | 6.18.57 |
| 7.2 | 7.2.11 |
| mainline | 7.3-rc7 |

A branch missing from this table, such as 6.8, received no upstream fix. Distribution kernels
backport fixes without changing the upstream version, so on Ubuntu, Debian and Red Hat the
version number alone does not answer the question. Use the checks below.

### Vendor status on 2026-10-19

| Vendor | Status |
|---|---|
| Ubuntu 22.04 (jammy) and 24.04 (noble) | "Needs evaluation" for every kernel package, no fixed version published |
| Debian 12 (bookworm) | fixed in bookworm-security `6.1.190-1` (DLA-4841-1) |
| Debian 13 (trixie) | fixed in trixie-security `6.12.113-1` (DSA-6568-1) |
| Red Hat Enterprise Linux | all tracked product streams marked affected |
| AWS | "There are no workarounds for this issue." Self-managed KVM hosts using virtio-net must patch |

No vendor names a mitigation short of a fixed kernel. A sysctl, a firewall rule or turning off
an offload is not a substitute, and a fixed kernel that is installed but not booted does not
help: the host is protected only after it reboots into the fixed kernel.

### What Orcastra shows

The dashboard checks the kernel every cluster member reports to LXD and judges it against each
advisory. Partners and Admins see the result on the cluster card as the number of CVEs still open
on that cluster, such as "1 CVE" or "2 CVEs". A cluster whose members are all patched shows nothing
on its card.

An open CVE is in one of two states on a member:

- Affected, when the version is known to lack the fix, for example an Ubuntu kernel while Ubuntu
  has published none;
- Unconfirmed, when the version string cannot prove it either way, for example a Debian, Red Hat
  or SUSE kernel whose backports are invisible in `uname -r`, a vendor build such as a Jetson
  `-tegra` kernel, or a member that could not be read.

The chip's icon follows the worst state on the cluster. Pointing at the chip, or reaching it with
the keyboard, lists each open CVE with its severity and how many members it affects. Clicking or
tapping it opens every member of the cluster with its kernel, state and reason. The System tab of
a cluster's details shows the result too, Patched included.

Affected and unconfirmed members also appear on the Kernel CVEs tab of Inventory > Warnings, one
row per member and CVE, with a CVE column to sort and filter on. A Kernel CVEs tile on the
overview's System Alerts card links there. The tile and the tab count those rows, so one CVE on
three members counts as three. `GET /api/v1/security/kernel-advisories` returns the same data,
where the states are named `exposed`, `unverified` and `patched`. Tenants do not see any of them.
Each result carries the command to run on the host and the date of the advisory data it was
judged against.

The Full installer's host preflight prints a `Kernel CVE-2026-99999` row. It passes when the
host can prove the fix and warns otherwise, including when a fixed kernel is installed but not
yet running. It never stops the install.

### Check a host

Run these on the LXD host itself, not inside an instance.

```bash
uname -r                      # the kernel that is running now
uname -v                      # on Debian this names the package version, such as "Debian 6.1.190-1"

# Ubuntu keeps the kernel's CVE list in the linux-modules changelog;
# the linux-image changelog belongs to the signed package and lists no CVEs
zgrep -m1 CVE-2026-99999 /usr/share/doc/linux-modules-$(uname -r)/changelog.Debian.gz
apt changelog linux-modules-$(uname -r) | grep -m1 CVE-2026-99999

dpkg-query -W 'linux-image-*'   # installed kernels, to compare with uname -r
ls /var/run/reboot-required     # present when an installed update waits for a reboot
canonical-livepatch status      # only on hosts enrolled in Livepatch
```

On Red Hat family hosts:

```bash
rpm -q --changelog kernel-core-$(uname -r) | grep -m1 CVE-2026-99999
```

A match means the running kernel carries the fix. No match on Ubuntu means it does not, and
on 2026-10-19 no Ubuntu kernel did. The Ubuntu tracker did not mention Livepatch coverage at
that date.

### Patch a host

Once your distribution publishes a fixed kernel, patch one host at a time. Members that keep
running carry the instances while one reboots.

=== "LXD cluster"

    ```bash
    lxc cluster evacuate <member>      # moves or stops its instances, per cluster.evacuate
    # on <member>:
    apt update && apt full-upgrade
    reboot
    # after it is back:
    uname -r                           # must be the fixed kernel
    zgrep -m1 CVE-2026-99999 /usr/share/doc/linux-modules-$(uname -r)/changelog.Debian.gz
    lxc cluster restore <member>
    ```

    Move to the next member only after `lxc cluster list` shows the restored one Online.

=== "Standalone LXD host"

    A standalone host has nowhere to move its instances, so plan a maintenance window. Stop the
    virtual machines cleanly first, then patch and reboot:

    ```bash
    lxc list --all-projects type=virtual-machine status=running
    lxc stop <instance> --project <project>
    apt update && apt full-upgrade
    reboot
    uname -r
    ```

    Instances you stopped yourself stay stopped after the reboot unless `boot.autostart` is
    true on them. Start them again and confirm they answer.

After patching, use the Refresh button in the dashboard's kernel dialog to re-read the hosts
instead of waiting for the cache to expire.

### Until a fix is available

There is no technical workaround. What reduces the risk on an exposed host is reducing who
can run code inside its virtual machines:

- hold back VM creation and console access on exposed hosts for organizations you do not trust;
- keep untrusted or third-party guest workloads off those hosts until they are patched;
- watch host logs for kernel warnings and unexpected crashes; a host crash is one of the
  published outcomes.

The published research targets KVM guests. None of the sources settle whether a container on
the same host can reach the same path, so treat the host kernel as the thing to fix either way.

### Keeping this advisory current

When Canonical publishes a fixed kernel, two places need its version: the dashboard's
advisory data in `backend/app/services/kernel_advisory_data.py` of the dashboard repository
(one entry per Ubuntu release and kernel series, then a deploy), and this page. The installer
check reads package changelogs on the host, so it recognises a fixed Ubuntu kernel without a
code change.

### References

- Linux kernel CVE announcement: <https://lists.openwall.net/linux-cve-announce/2026/10/15/2>
- Upstream fix: <https://git.kernel.org/stable/c/6785011f8b16abf40b1e9d8b2e332232b3e68822>
- Ubuntu: <https://ubuntu.com/security/CVE-2026-99999>
- Debian: <https://security-tracker.debian.org/tracker/CVE-2026-99999>
- Red Hat: <https://access.redhat.com/security/cve/cve-2026-99999>
- AWS security bulletin 2026-137-AWS: <https://aws.amazon.com/security/security-bulletins/2026-137-aws/>
