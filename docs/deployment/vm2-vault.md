# VM 2 - Vault (Secrets)

**Specifications:** 2 vCPU, 2 GB RAM, 20 GB Storage

HashiCorp Vault provides secret management (KV v2 engine) and PKI certificate authority for the Orcastra platform. Vault runs as a native service (not Docker) and forwards audit logs to OpenSearch via Fluent Bit.

---

## Step 1: Install Vault

```bash
# Add HashiCorp GPG key and repository
wget -O - https://apt.releases.hashicorp.com/gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg

echo "deb [arch=$(dpkg --print-architecture) \
  signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] \
  https://apt.releases.hashicorp.com \
  $(grep -oP '(?<=UBUNTU_CODENAME=).*' /etc/os-release || lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/hashicorp.list

sudo apt update && sudo apt install vault
```

!!! tip
    If you see "failed: Network is unreachable", retry the command.

---

## Step 2: Configure Vault

Edit the Vault configuration file:

```bash
nano /etc/vault.d/vault.hcl
```

Replace the contents with:

```hcl
ui            = true
disable_mlock = true
api_addr      = "http://<VM2_PRIVATE_IP>:8200"
cluster_addr  = "http://<VM2_PRIVATE_IP>:8201"

# Integrated storage (raft), kept in the directory the package created
storage "raft" {
  path    = "/opt/vault/data"
  node_id = "vm2-vault"
}

# HTTP listener (for internal LXD network use)
listener "tcp" {
  address         = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"
  tls_disable     = 1
}

# HTTPS listener (use instead of the one above for production with TLS)
# listener "tcp" {
#   address         = "0.0.0.0:8200"
#   cluster_address = "0.0.0.0:8201"
#   tls_cert_file   = "/opt/vault/tls/tls.crt"
#   tls_key_file    = "/opt/vault/tls/tls.key"
# }
```

Replace `<VM2_PRIVATE_IP>` with this VM's private address. Vault refuses to start without a
`storage` block (`A storage backend must be specified`). Raft keeps the data in
`/opt/vault/data`, which the package creates and owns as the `vault` user. `disable_mlock`
is the recommended setting with raft.

!!! warning "TLS Consideration"
    TLS is disabled here because traffic travels over the internal LXD bridge network. For internet-facing deployments, enable TLS.

---

## Step 3: Initialize and Unseal

```bash
systemctl enable vault
systemctl start vault
systemctl status vault
```

Set the Vault address and initialize:

```bash
export VAULT_ADDR='http://127.0.0.1:8200'
vault operator init
```

!!! danger "Save These Immediately"
    The `init` command outputs **5 unseal keys** and **1 initial root token**. Copy and store them securely. You will need:

    - **3 of 5 unseal keys** to unseal Vault after every restart
    - **Root token** for initial configuration

Unseal Vault (requires 3 keys):

```bash
vault operator unseal   # Paste Key 1, press Enter
vault operator unseal   # Paste Key 2, press Enter
vault operator unseal   # Paste Key 3, press Enter
```

Verify Vault is unsealed:

```bash
vault status
```

The output should show `Sealed: false`.

!!! note "A few seconds after unsealing"
    With raft, a freshly unsealed node needs a few seconds to become the active node. A command
    that answers `local node not active but active cluster node not found` in that window
    succeeds when repeated. `vault status` shows `HA Mode active` once it is ready.

---

## Step 4: Configure Secret Engines

Login with the root token:

```bash
apt update && apt install -y jq
export VAULT_ADDR='http://127.0.0.1:8200'
vault login   # Enter your root token
```

### 4a. Enable KV v2 Secret Engine

```bash
vault secrets enable -path=secret kv-v2
```

### 4b. Setup Root PKI

```bash
vault secrets enable pki
vault secrets tune -max-lease-ttl=87600h pki
```

### 4c. Generate Root CA

```bash
vault write pki/root/generate/internal \
  common_name="Orcastra Root CA" \
  ttl=87600h
```

### 4d. Setup Intermediate PKI

```bash
vault secrets enable -path=pki_int pki
vault secrets tune -max-lease-ttl=43800h pki_int
```

### 4e. Generate and Sign Intermediate CA

```bash
# Generate CSR
vault write -format=json \
  pki_int/intermediate/generate/internal \
  common_name="Orcastra Intermediate CA" \
  | jq -r '.data.csr' > /tmp/pki_int.csr

# Sign with Root CA
vault write -format=json \
  pki/root/sign-intermediate \
  csr=@/tmp/pki_int.csr \
  format=pem_bundle \
  ttl=43800h \
  | jq -r '.data.certificate' > /tmp/intermediate.cert.pem

# Import signed certificate
vault write pki_int/intermediate/set-signed \
  certificate=@/tmp/intermediate.cert.pem
```

### 4f. Create LXD Certificate Role

```bash
vault write pki_int/roles/lxd \
  allowed_domains="orcastra.io,lxd.local" \
  allow_subdomains=true \
  allow_any_name=true \
  max_ttl=8760h \
  key_type=ec \
  key_bits=384
```

---

## Step 5: Create Policy and Token

### Create the Orcastra Policy

```bash
cat > /tmp/orcastra-policy.hcl << 'POLICY'
path "secret/data/clusters/*"     { capabilities = ["create","read","update","delete","list"] }
path "secret/metadata/clusters/*" { capabilities = ["list","read","delete"] }
path "pki_int/issue/lxd"          { capabilities = ["create","update"] }
path "pki_int/certs"              { capabilities = ["list"] }
path "secret/data/orcastra/*"     { capabilities = ["create","read","update"] }
path "secret/data/integrations/*" { capabilities = ["create","read","update","delete"] }
path "secret/metadata/integrations/*" { capabilities = ["list","read","delete"] }
path "secret/data/my_keys/*"      { capabilities = ["create","read","update","delete","list"] }
path "secret/metadata/my_keys/*"  { capabilities = ["list","read","delete"] }
POLICY
```

### Apply the Policy

```bash
vault policy write orcastra-policy /tmp/orcastra-policy.hcl
```

### Create Dashboard Token

```bash
vault token create \
  -orphan \
  -policy=orcastra-policy \
  -period=720h \
  -display-name="orcastra-dashboard"
```

!!! warning "No dashboard token stays valid on its own"
    A non-root token created with `-ttl=0` is not permanent. Vault gives it the default
    lease of 768h, so it expires after 32 days (`vault token lookup` shows the
    `expire_time`). The backend never renews its token, so once it expires every Vault call
    returns `403` and cluster registration and certificate issuance stop. Create a periodic
    token as above and renew it on a schedule, see
    [Renew the Dashboard Token](#renew-the-dashboard-token). Each renewal resets the 30-day
    period, and the token keeps the `default` policy that allows it to renew itself.

### Verify Dashboard Token Permissions

After generating the dashboard token (`hvs...`), verify access to integrations paths:

```bash
export VAULT_TOKEN=<DASHBOARD_TOKEN_FROM_OUTPUT>
vault kv list secret/integrations/api_keys
```

Expected output for first-time setup:

```text
No value found at secret/metadata/integrations/api_keys
```

!!! warning "If You See 403 Permission Denied"
    The policy is missing integrations paths. Re-apply `orcastra-policy` and ensure both paths below exist:
    - `secret/data/integrations/*`
    - `secret/metadata/integrations/*`

!!! danger "Save the Token"
    The output shows a `token` field starting with `hvs.` - this is your `VAULT_TOKEN` for the Dashboard `.env` on VM 4.

### Renew the Dashboard Token

Once the dashboard `.env` exists on **VM 4** ([Step 6 there](vm4-dashboard.md)), add a daily
job on VM 4 that renews the token:

```bash
cat > /etc/cron.daily/orcastra-vault-token-renew << 'RENEW'
#!/bin/sh
# Renew the dashboard's periodic Vault token, resetting its 30-day period.
ENV=/root/orcastra/.env
VAULT_ADDR=$(grep '^VAULT_ADDR=' "$ENV" | cut -d= -f2-)
VAULT_TOKEN=$(grep '^VAULT_TOKEN=' "$ENV" | cut -d= -f2-)
curl -sf -X POST -H "X-Vault-Token: $VAULT_TOKEN" "$VAULT_ADDR/v1/auth/token/renew-self" >/dev/null \
  || logger -t orcastra "Vault dashboard token renewal failed"
RENEW
chmod 700 /etc/cron.daily/orcastra-vault-token-renew
/etc/cron.daily/orcastra-vault-token-renew && echo renewed
```

The [automated installer](automated-install.md) renews it from the LXD host instead.

---

## Step 6: Enable Audit Logging

Vault audit logs are forwarded to OpenSearch (VM 3) via Fluent Bit for centralized security monitoring.

### Enable Audit Device

```bash
export VAULT_ADDR='http://127.0.0.1:8200'
unset VAULT_TOKEN   # Clear the dashboard token from Step 5 so the root login below takes effect
vault login   # Enter root token

mkdir -p /var/log/vault
chown vault:vault /var/log/vault
chmod 750 /var/log/vault

vault audit enable file file_path=/var/log/vault/audit.log
```

!!! warning "403 Permission Denied When Enabling Audit"
    If `vault login` prints `WARNING! The VAULT_TOKEN environment variable is set!` and
    `vault audit enable` then fails with `Code: 403 ... permission denied`, the `VAULT_TOKEN`
    you exported in [Step 5](#verify-dashboard-token-permissions) is still set and **takes
    precedence over the root token** from `vault login`. The dashboard token uses
    `orcastra-policy`, which has no `sys/audit` access. Run `unset VAULT_TOKEN`, then re-run the
    audit command.

### Configure Logrotate

Create `/etc/logrotate.d/vault-audit`:

```bash
nano /etc/logrotate.d/vault-audit
```

```
/var/log/vault/audit.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    maxsize 100M
}
```

---

## Step 7: Install Fluent Bit

Fluent Bit runs natively on VM 2 to read Vault audit logs and forward them to OpenSearch on VM 3.

### Install

```bash
curl https://raw.githubusercontent.com/fluent/fluent-bit/master/install.sh | sh
```

!!! tip
    If the download fails with a DNS error, retry the command.

### Configure Fluent Bit

Edit `/etc/fluent-bit/fluent-bit.conf`:

```bash
nano /etc/fluent-bit/fluent-bit.conf
```

```ini
[SERVICE]
    Flush        1
    Daemon       Off
    Log_Level    info
    Parsers_File parsers.conf
    # Filesystem buffering so a multi-day OpenSearch outage never drops audit logs
    storage.path              /var/lib/fluent-bit/storage/
    storage.sync              normal
    storage.checksum          off
    storage.backlog.mem_limit 128M

[INPUT]
    Name              tail
    Path              /var/log/vault/audit.log
    Tag               vault.audit
    Parser            vault_json
    DB                /var/lib/fluent-bit/vault.db
    Mem_Buf_Limit     10MB
    Refresh_Interval  5
    storage.type      filesystem

[OUTPUT]
    Name              opensearch
    Match             vault.audit
    Host              <VM3_PRIVATE_IP>
    Port              9200
    HTTP_User         fluentbit
    HTTP_Passwd       <FLUENTBIT_PASSWORD_FROM_VM3>
    tls               On
    # Verify VM 3 against its private CA (VM 3 Step 5); the node certificate carries VM3_PRIVATE_IP.
    tls.verify        On
    tls.ca_file       /etc/fluent-bit/orcastra-logging-ca.pem
    Suppress_Type_Name On
    net.connect_timeout       10
    net.keepalive             on
    net.keepalive_idle_timeout 30
    Logstash_Format   On
    Logstash_Prefix   vault-audit
    # One index per month: Vault audit is kept 3 years, and one index per day would exceed the
    # single-node shard limit (see VM 3 Step 9).
    Logstash_DateFormat %Y.%m
    # Vault audit logs must never be dropped (compliance): unlimited retries + disk backlog
    Retry_Limit       no_limits
    storage.total_limit_size  4G
    Buffer_Size       5MB
    Trace_Error       On
    Replace_Dots      On
    Generate_ID       On
```

!!! warning "Placeholder Values"
    Replace `<VM3_PRIVATE_IP>` and `<FLUENTBIT_PASSWORD_FROM_VM3>` with actual values from [VM 3 setup](vm3-opensearch.md).
    VM 3 is deployed after this VM, so finish this output (and the CA file below) once VM 3 Step 11
    is done; until then Fluent Bit keeps the audit log buffered on disk.

    The Fluent Bit password is a single shared credential used in **three** places:
    the `fluentbit` user in OpenSearch (VM 3), this literal `HTTP_Passwd` (VM 2), and
    `OPENSEARCH_PASSWORD` in the dashboard `.env` (VM 4). Rotating it means updating
    all three at once, or shipping silently breaks (see
    [Troubleshooting](../operations/troubleshooting.md)).

### Install the Logging CA Certificate

Copy `certs/root-ca.pem` from VM 3 (Step 11 prints it) to this VM:

```bash
nano /etc/fluent-bit/orcastra-logging-ca.pem    # paste the certificate, including the BEGIN/END lines
chmod 644 /etc/fluent-bit/orcastra-logging-ca.pem
openssl x509 -in /etc/fluent-bit/orcastra-logging-ca.pem -noout -subject
```

The subject should read `CN = Orcastra Logging Root CA`.

### Configure Parser

Edit `/etc/fluent-bit/parsers.conf`:

```bash
nano /etc/fluent-bit/parsers.conf
```

Ensure it contains:

```ini
[PARSER]
    Name        vault_json
    Format      json
    Time_Key    time
    Time_Format %Y-%m-%dT%H:%M:%S.%L%z
    Time_Keep   On
```

### Start Fluent Bit

```bash
mkdir -p /var/lib/fluent-bit/storage
systemctl enable fluent-bit
systemctl start fluent-bit
systemctl status fluent-bit
```

The status should show `active (running)`.

### Verify Log Forwarding

```bash
# Generate a test audit event (any authenticated call writes to the audit log)
export VAULT_ADDR='http://127.0.0.1:8200'
vault audit list

# Check audit log growth
sleep 5
wc -l /var/log/vault/audit.log

# Check Fluent Bit for errors
journalctl -u fluent-bit --no-pager -n 20
```

!!! tip
    If `vault read` shows an HTTPS error, ensure you've set `export VAULT_ADDR='http://127.0.0.1:8200'` (HTTP, not HTTPS).

---

## Output Summary

After completing VM 2 setup, you should have the following values saved:

| Value | Environment Variable (VM 4) | Notes |
|---|---|---|
| 5 × Unseal Keys | - | Required after every Vault restart |
| Root Token | - | Admin access (store securely) |
| Dashboard Token (`hvs.…`) | `VAULT_TOKEN` | Scoped to `orcastra-policy` |
| Vault Address | `VAULT_ADDR` | `http://<VM2_IP>:8200` |

---

**Next:** [VM 3 - OpenSearch (Logging)](vm3-opensearch.md)
