# ORCA Agent Installer

The ORCA Agent connects a cluster to OrcaHub so its services can be exposed. You do not assemble the
installer by hand, OrcaHub generates a one-time, signed, checksum-verified installer for you and
fills in the credentials at install time.

## Install

1. In OrcaHub, open the **Register ORCA Agent** flow (Network -> Expose Services).
2. Choose the target cluster and set an agent name.
3. Copy the generated installer command and run it on the cluster-side host as root:

```bash
BOOTSTRAP_URL='<copied-from-orcahub>'
curl -fsSL "$BOOTSTRAP_URL" -o /tmp/orca-agent-install.sh \
  && chmod 700 /tmp/orca-agent-install.sh \
  && bash /tmp/orca-agent-install.sh
```

!!! warning "The link expires quickly"
    The bootstrap link is short-lived and single-use. Copy and run the command soon after opening
    the dialog. If it expires, reopen the dialog to generate a new one.

The installer downloads a version-pinned agent, verifies its checksum, installs it as a service, and
registers it with OrcaHub. Credentials are injected by OrcaHub at install time, not carried in the
link.

## Verify

```bash
systemctl status orca-agent
journalctl -u orca-agent -e
```

The agent reports back to OrcaHub once it is running. The cluster then shows as online in the
**Expose Services** view.

!!! note "Keep generated files private"
    The installer writes a local environment file containing tokens. Treat it as secret, keep it out
    of source control and backups, and rotate agent credentials from OrcaHub when needed.
