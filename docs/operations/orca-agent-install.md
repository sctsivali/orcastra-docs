# ORCA Agent Installer Script

Use this helper script to generate a secure environment file for ORCA Agent registration and runtime commands.

## Script Location

- Repository path: `scripts/orca-agent/install.sh`
- Raw URL (main branch): `https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/scripts/orca-agent/install.sh`

## Recommended Usage

Use OrcaHub's generated short-lived bootstrap URL and let this script fetch the full installer logic securely.

```bash
INSTALLER_SCRIPT_URL="https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/scripts/orca-agent/install.sh"
BOOTSTRAP_URL="<COPY_FROM_ORCAHUB_REGISTER_DIALOG>"

curl -fsSL "$INSTALLER_SCRIPT_URL" -o /tmp/orca-agent-install.sh
chmod +x /tmp/orca-agent-install.sh
/tmp/orca-agent-install.sh --bootstrap-url "$BOOTSTRAP_URL"
```

## Manual Usage

Pin a script version and validate checksum before running it.

```bash
SCRIPT_VERSION="main"
SCRIPT_URL="https://raw.githubusercontent.com/sctsivali/orcastra-docs/${SCRIPT_VERSION}/scripts/orca-agent/install.sh"

curl -fsSL "$SCRIPT_URL" -o /tmp/orca-agent-install.sh
chmod +x /tmp/orca-agent-install.sh

# Optional but recommended: validate SHA256 first
# echo "<SHA256>  /tmp/orca-agent-install.sh" | sha256sum -c -

/tmp/orca-agent-install.sh \
  --api-url "https://api.orcahub.example" \
  --enrollment-token "<ENROLLMENT_TOKEN>" \
  --agent-name "my-node-orca-agent" \
  --frpc-token "<FRPC_TOKEN>" \
  --cluster-id "<ORCASTRA_CLUSTER_ID>" \
  --output "/etc/orca-agent/setup.env"
```

## One-liner Variant

If you intentionally use manual flags instead of OrcaHub bootstrap mode:

```bash
curl -fsSL "https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/scripts/orca-agent/install.sh" \
  | bash -s -- \
    --api-url "https://api.orcahub.example" \
    --enrollment-token "<ENROLLMENT_TOKEN>" \
    --agent-name "my-node-orca-agent" \
    --frpc-token "<FRPC_TOKEN>" \
    --cluster-id "<ORCASTRA_CLUSTER_ID>" \
    --output "/etc/orca-agent/setup.env"
```

## Output

The script creates an env file (default: `/etc/orca-agent/setup.env`) with strict file permission `600`.

Generated keys:

- `ORCAHUB_API_URL`
- `ORCA_AGENT_ENROLLMENT_TOKEN`
- `ORCA_AGENT_NAME`
- `ORCA_AGENT_FRPC_TOKEN`
- `ORCA_AGENT_CLUSTER_ID`

## Security Notes

- Treat the output file as secret material.
- Do not commit the generated env file into source control.
- Rotate enrollment and FRPC tokens periodically.
- Prefer pinned versions (`main` can move).
