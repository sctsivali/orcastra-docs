"""Exception taxonomy. Every fatal error carries a human remediation string that the
top-level handler prints verbatim, so failures are actionable rather than a stack trace."""


class InstallError(Exception):
    """Base for all installer failures. `remediation` is shown to the operator."""

    def __init__(self, message: str, *, phase: str = None, remediation: str = None):
        super().__init__(message)
        self.message = message
        self.phase = phase
        self.remediation = remediation


class PreflightError(InstallError):
    """A blocking preflight check failed (OS, tooling, ports, privilege)."""


class DockerError(InstallError):
    """Docker engine/compose/registry problem."""


class ConfigError(InstallError):
    """Invalid or incoherent configuration (e.g. SAN vs URL mismatch)."""


class VaultError(InstallError):
    """Vault init/unseal/PKI failure."""


class BootstrapError(InstallError):
    """Admin bootstrap failure."""


class VerifyError(InstallError):
    """Post-install verification failed."""


class AbortByUser(InstallError):
    """The operator declined a required confirmation, or non-interactive mode lacked a
    needed answer. Not a bug - a clean, expected stop."""
