"""cloud-init user-data shared by all four instances. It holds only the operator's PUBLIC
key, never a secret (instance config is readable by anyone with LXD access)."""
from __future__ import annotations

import json

_SSHD = """# Managed by orcastra-full
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers ubuntu
X11Forwarding no
"""

_SYSCTL_VM = """# Managed by orcastra-full: OpenSearch needs this (docs/deployment/vm3-opensearch.md)
vm.max_map_count = 262144
"""


def user_data(pubkey: str, *, vm: bool) -> str:
    files = [{"path": "/etc/ssh/sshd_config.d/60-orcastra.conf", "permissions": "0644",
              "content": _SSHD}]
    cmds = [["sh", "-c", "systemctl restart ssh || systemctl restart sshd || true"]]
    if vm:
        files.append({"path": "/etc/sysctl.d/60-orcastra.conf", "permissions": "0644",
                      "content": _SYSCTL_VM})
        cmds.append(["sysctl", "--system"])
    doc = {
        "users": [{
            "name": "ubuntu", "groups": "sudo", "shell": "/bin/bash", "lock_passwd": True,
            "sudo": "ALL=(ALL) NOPASSWD:ALL", "ssh_authorized_keys": [pubkey],
        }],
        "ssh_pwauth": False,
        "disable_root": True,
        "package_update": True,
        "packages": ["ca-certificates", "curl", "gnupg", "jq", "nftables", "python3"],
        "write_files": files,
        "runcmd": cmds,
    }
    # cloud-init accepts JSON documents after the #cloud-config header (YAML superset)
    return "#cloud-config\n" + json.dumps(doc, indent=2) + "\n"
