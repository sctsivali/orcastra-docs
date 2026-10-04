# VM 3 - OpenSearch (Logging)

**Specifications:** 4 vCPU, 16 GB RAM, 100 GB Storage

OpenSearch is the central log store of the platform. It receives the Orcastra Dashboard logs
(VM 4) and the Vault audit log (VM 2) through Fluent Bit, and can also hold the Docker container
logs of other stacks you run next to Orcastra. OpenSearch Dashboards is the log UI, with sign-in
through Authentik (VM 1).

Everything this VM runs is versioned in the `orcastra-cmp` repository under `deploy/logging/`:
the compose file (images pinned by digest), node and Dashboards configuration, security roles,
index templates, ingest pipelines, and the scripts used below. Its `docs/runbook.md` covers
day-to-day operation.

---

## Prerequisites

!!! warning "Host-Level Configuration Required"
    Before creating the VM, run this on the **LXD host server** (not inside the container):

    ```bash
    sudo sysctl -w vm.max_map_count=262144
    echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
    sudo sysctl -p
    ```

    OpenSearch requires this memory mapping setting and will fail to start without it.

- Docker with the compose plugin: follow the [common Docker installation](index.md#common-docker-installation).
- Inside an LXD container, use the `vfs` storage driver (`/etc/docker/daemon.json`:
  `{"storage-driver": "vfs"}`, then `systemctl restart docker`).
- VM 1 (Authentik) is running and you have an Authentik API token with admin rights.
- `python3` and `openssl` on the VM (present on Ubuntu 24.04).

---

## Step 1: Install the Deployment Bundle

Copy `deploy/logging/` from `orcastra-cmp` to the VM:

```bash
# from a machine with the orcastra-cmp checkout
rsync -a --exclude certs/ --exclude secrets/ --exclude compose/.env \
  deploy/logging/ root@<VM3_IP>:/opt/orcastra-logging/
```

All remaining commands run on VM 3 as root in `/opt/orcastra-logging`.

Review the values that are specific to your network before going further:

| File | Setting |
|---|---|
| `scripts/gen-certs.sh` | `NODE_SAN`: every name and IP clients use to reach OpenSearch |
| `compose/docker-compose.yml` | the private IP Dashboards binds to (`<VM3_PRIVATE_IP>:5601`) |
| `opensearch/security/config.yml` | Authentik discovery URL, `required_issuer`, `required_audience` |
| `dashboards/opensearch_dashboards.yml` | Authentik URLs, `client_id`, public base URL |

---

## Step 2: Certificates

OpenSearch must not run with its demo certificates: their private keys are public, and anyone
who can reach port 9200 could use the demo admin certificate to take over the security
configuration. Create a private CA and the node and admin certificates:

```bash
scripts/gen-certs.sh
```

!!! danger "Back up the CA key"
    Copy `certs/root-ca-key.pem` to offline storage. Without it, renewing certificates means
    re-issuing every certificate and redistributing the CA to all clients.

`certs/root-ca.pem` is public: clients that verify TLS (recommended) use it.

---

## Step 3: Passwords and Volumes

```bash
umask 077
mkdir -p secrets
echo "OPENSEARCH_ADMIN_PASSWORD=$(openssl rand -base64 33 | tr -d '/+=' | cut -c1-32)" > compose/.env
openssl rand -base64 33 | tr -d '/+=\n' | cut -c1-32 > secrets/kibanaserver_password

# Data and snapshot volumes (the names are fixed by the compose file)
mkdir -p /opt/opensearch/archive && chown 1000:1000 /opt/opensearch/archive
docker volume create root_opensearch-data
docker volume create --driver local --opt type=none --opt o=bind \
  --opt device=/opt/opensearch/archive root_opensearch-snapshots
```

- **Admin password**: the break-glass account. Store it in your password manager.
- **kibanaserver password**: used only by Dashboards to talk to OpenSearch.

---

## Step 4: Authentik Application

Run from a machine that can reach Authentik:

```bash
AUTHENTIK_URL=https://<AUTHENTIK_DOMAIN> AUTHENTIK_TOKEN=<admin API token> \
  scripts/authentik-provision.py --admins <your-username> --secret-out - \
  | ssh root@<VM3_IP> 'umask 077; cat > /opt/orcastra-logging/secrets/opensearch_oidc_client_secret'
```

This creates the groups `opensearch-admins` (full access) and `opensearch-viewers` (read-only),
the OAuth2 provider, and the `opensearch-dashboards` application, restricted to those two groups.
It prints the client ID: put it in `opensearch/security/config.yml` (`required_audience`) and
`dashboards/opensearch_dashboards.yml` (`opensearch_security.openid.client_id`). The client
secret goes straight to the VM and is never displayed. The script is idempotent.

---

## Step 5: Start OpenSearch and Initialise Security

```bash
scripts/dashboards-keystore.sh                          # Dashboards secrets keystore
docker compose -f compose/docker-compose.yml up -d opensearch
docker compose -f compose/docker-compose.yml ps         # wait until healthy (about 1-2 minutes)
scripts/init-security.sh                                 # fresh cluster only
scripts/apply-ingest-config.sh                           # index templates + ingest pipelines
docker compose -f compose/docker-compose.yml up -d opensearch-dashboards
```

`init-security.sh` refuses to run against a cluster that is already initialised; existing
clusters are updated with `scripts/apply-security.sh`, which backs up the live configuration
first.

---

## Step 6: Fluent Bit Writer Accounts

The Orcastra Dashboard (VM 4) and Vault (VM 2) ship with the `fluentbit` account (backend role
`log_writer`):

```bash
source scripts/lib.sh
FB_PASS="$(openssl rand -base64 33 | tr -d '/+=' | cut -c1-32)"
os PUT /_plugins/_security/api/internalusers/fluentbit -H 'content-type: application/json' \
  -d "{\"password\":\"$FB_PASS\",\"backend_roles\":[\"log_writer\"]}"
echo "$FB_PASS"    # set as OPENSEARCH_PASSWORD on VM 2 and VM 4, then clear your scrollback
```

Container logs from another stack get their own write-only account:
`scripts/provision-stack.sh <stack> <env>` (see `docs/onboarding-a-stack.md` in the bundle).

---

## Step 7: Dashboard Objects

```bash
# Orcastra dashboards (from orcastra-cmp)
DASHBOARDS_URL=http://localhost:5601 OPENSEARCH_ADMIN_PASSWORD=<admin password> \
  ./setup_opensearch_dashboards.sh
# containers-* index pattern and the "Container errors" saved search
scripts/dashboards-objects.sh
```

!!! note "Global tenant"
    SSO users land in the **Global** tenant. Import shared dashboards there (the setup script uses
    the signed-in user's preferred tenant, which is Global in this configuration); objects in a
    user's Private tenant are visible only to that user.

---

## Step 8: Verify

```bash
scripts/verify.sh
```

It checks cluster health, the shard budget, disk and heap, that the node certificate chains to
your CA (and is not the demo certificate), ingest freshness, the authentication domains, port
exposure, and that the SSO login redirects to Authentik.

---

## Index Layout

Indices are **monthly** (`orcastra-access-YYYY.MM`, `orcastra-audit-YYYY.MM`,
`orcastra-app-YYYY.MM`, `vault-audit-YYYY.MM`, `containers-<stack>-<env>-YYYY.MM`). Fluent Bit
keeps sending daily Logstash-style names; an ingest pipeline on each index template routes the
document to the monthly index. A single node has a limit of 1000 shards, and one daily index
per log type exhausts it in well under a year.

## Access

- **People** sign in with Authentik ("Log in with Orcastra SSO"). Members of
  `opensearch-admins` get full access; members of `opensearch-viewers` get read-only access.
- **Break-glass**: the internal `admin` account can still sign in with a password. If Authentik
  is unreachable when Dashboards restarts, Dashboards will not start; the runbook describes the
  break-glass Dashboards instance for that case.

---

## Output Summary

| Value | Used On | Environment Variable (VM 4) |
|---|---|---|
| OpenSearch admin password | VM 3 (break-glass, admin API) | `OPENSEARCH_ADMIN_PASSWORD` (archive feature only) |
| Fluent Bit password | VM 2, VM 4 | `OPENSEARCH_PASSWORD` |
| OpenSearch IP | VM 2, VM 4 | `OPENSEARCH_HOST` |
| CA certificate (`certs/root-ca.pem`) | VM 2, VM 4 (when verifying TLS) | - |

---

**Next:** [VM 4 - Orcastra Dashboard](vm4-dashboard.md)
