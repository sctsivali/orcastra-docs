"""Orcastra CMP Full automated installer (Python stdlib only).

Runs on an LXD host and builds the four-instance deployment described in
docs/deployment (Authentik, Vault, OpenSearch, Orcastra CMP): instances, static
addresses, SSH bastion access, per-instance firewalls, every cross-instance secret,
the four components, the LXD port forwards, an unseal watchdog and an end-to-end
verification. Re-runs resume where a previous run stopped.
"""

__version__ = "1.0.0-RC2"
