"""Shared building blocks for the Orcastra installers (Mini and Full).

Stdlib only. Everything here is deployment-agnostic: logging with secret redaction, a
subprocess wrapper, prompts that work under `curl | bash`, the phase state ledger, atomic
file writes, network helpers and retry.
"""
