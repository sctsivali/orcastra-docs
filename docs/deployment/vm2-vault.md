# VM 2 — Vault (Secrets)

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
# HTTP listener (for internal LXD network use)
listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = 1
}

# HTTPS listener (uncomment for production with TLS)
# listener "tcp" {
#   address       = "0.0.0.0:8200"
#   tls_cert_file = "/opt/vault/tls/tls.crt"
#   tls_key_file  = "/opt/vault/tls/tls.key"
# }
```

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
POLICY
```

### Apply the Policy

```bash
vault policy write orcastra-policy /tmp/orcastra-policy.hcl
```

### Create Dashboard Token

```bash
vault token create \
  -policy=orcastra-policy \
  -period=8760h \
  -display-name="orcastra-dashboard"
```

!!! danger "Save the Token"
    The output shows a `token` field starting with `hvs.` — this is your `VAULT_TOKEN` for the Dashboard `.env` on VM 4.

---

## Step 6: Enable Audit Logging

Vault audit logs are forwarded to OpenSearch (VM 3) via Fluent Bit for centralized security monitoring.

### Enable Audit Device

```bash
export VAULT_ADDR='http://127.0.0.1:8200'
vault login   # Enter root token

mkdir -p /var/log/vault
chown vault:vault /var/log/vault
chmod 750 /var/log/vault

vault audit enable file file_path=/var/log/vault/audit.log
```

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

[INPUT]
    Name              tail
    Path              /var/log/vault/audit.log
    Tag               vault.audit
    Parser            vault_json
    DB                /var/lib/fluent-bit/vault.db
    Mem_Buf_Limit     10MB
    Refresh_Interval  5

[OUTPUT]
    Name              opensearch
    Match             vault.audit
    Host              <VM3_PRIVATE_IP>
    Port              9200
    HTTP_User         fluentbit
    HTTP_Passwd       <FLUENTBIT_PASSWORD_FROM_VM3>
    tls               On
    tls.verify        Off
    Suppress_Type_Name On
    Logstash_Format   On
    Logstash_Prefix   vault-audit
    Logstash_DateFormat %Y.%m.%d
    Retry_Limit       5
    Buffer_Size       5MB
    Trace_Error       On
    Replace_Dots      On
    Generate_ID       On
```

!!! warning "Placeholder Values"
    Replace `<VM3_PRIVATE_IP>` and `<FLUENTBIT_PASSWORD_FROM_VM3>` with actual values from [VM 3 setup](vm3-opensearch.md).

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
mkdir -p /var/lib/fluent-bit
systemctl enable fluent-bit
systemctl start fluent-bit
systemctl status fluent-bit
```

The status should show `active (running)`.

### Verify Log Forwarding

```bash
# Generate a test audit event
export VAULT_ADDR='http://127.0.0.1:8200'
vault read sys/audit

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
| 5 × Unseal Keys | — | Required after every Vault restart |
| Root Token | — | Admin access (store securely) |
| Dashboard Token (`hvs.…`) | `VAULT_TOKEN` | Scoped to `orcastra-policy` |
| Vault Address | `VAULT_ADDR` | `http://<VM2_IP>:8200` |

---

**Next:** [VM 3 — OpenSearch (Logging)](vm3-opensearch.md)
