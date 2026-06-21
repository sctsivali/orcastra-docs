"""Orcastra Mini automated installer (Python stdlib only).

Replicates the manual quick-start (docs/mini/quick-start.md) end to end: preflight,
optional Docker install, config wizard, secret/cert generation, Vault init/unseal/PKI,
admin bootstrap, and verification - with idempotent re-runs and a non-interactive mode.
"""

__version__ = "1.0.0"
