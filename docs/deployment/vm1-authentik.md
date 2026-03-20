# VM 1 - Authentik (SSO)

**Specifications:** 2 vCPU, 4 GB RAM, 40 GB Storage

Authentik provides Single Sign-On (SSO) via OAuth2/OpenID Connect and manages user identity and role groups for the Orcastra platform.

---

## Step 1: Install Docker

Follow the [common Docker installation](index.md#common-docker-installation) steps.

---

## Step 2: Deploy Authentik

### Download Compose File

```bash
wget https://docs.goauthentik.io/compose.yml
```

!!! tip
    If `wget` fails with "Network is unreachable", retry until the download completes.

### Generate Secrets

```bash
echo "PG_PASS=$(openssl rand -base64 36 | tr -d '\n')" >> .env
echo "AUTHENTIK_SECRET_KEY=$(openssl rand -base64 60 | tr -d '\n')" >> .env
echo "AUTHENTIK_ERROR_REPORTING__ENABLED=true" >> .env
```

### Start Authentik

```bash
docker compose pull
docker compose up -d
```

!!! note "Pull Errors"
    If you see `Error failed to resolve reference "ghcr.io/goauthentik/server:..."`, retry the pull command.

---

## Step 3: Create Admin Account

1. Open your browser: `http://<VM1_IP>:9000/if/flow/initial-setup/`

    !!! warning "Trailing Slash Required"
        The URL **must** end with `/` or you will get a "Not Found" error.

2. Fill in the initial setup form:
    - **Email:** `admin@orcastra.io` (or your preferred email)
    - **Password:** Set a strong password

3. Click **Continue**

!!! danger "Save This Password"
    This is your `akadmin` password. Store it securely - you will need it for all admin operations.

---

## Step 4: Configure OAuth2 Provider

### 4a. Create the Provider

1. Open Authentik admin: `http://<VM1_IP>:9000/if/admin/`
2. Login with `akadmin` and the password from Step 3
3. Navigate to **Applications** → **Providers** in the left sidebar
4. Click **Create** → select **OAuth2/OpenID Provider** → **Next**
5. Configure the provider:

    | Field | Value |
    |---|---|
    | Name | `Orcastra Dashboard Provider` |
    | Authorization flow | `default-provider-authorization-implicit-consent` |
    | Client type | `Confidential` |
    | Client ID | *(auto-generated - copy and save)* |
    | Client Secret | *(auto-generated - copy and save)* |

6. Set **Redirect URIs/Origins** (one per line):

    ```
    http://<VM4_IP>:4321/api/auth/callback/authentik
    https://app.orcastra.io/api/auth/callback/authentik
    ```

    !!! info
        The first line is for direct IP access. The second is for custom domain access (see [Domain Setup](../operations/domain-setup.md)).

7. Under **Advanced protocol settings**:
    - **Scopes:** ensure `openid`, `profile`, `email`, `offline_access` are selected
    - **Subject mode:** `Based on the User's hashed ID`
    - **Include claims in id_token:** :material-toggle-switch: **Enabled** (required for groups to appear in token)

8. Click **Finish**

!!! danger "Save These Values"
    - **Client ID** → used as `AUTHENTIK_CLIENT_ID` on VM 4
    - **Client Secret** → used as `AUTHENTIK_CLIENT_SECRET` on VM 4

### 4b. Create the Application

1. Navigate to **Applications** → **Applications**
2. Click **Create**
3. Configure:

    | Field | Value |
    |---|---|
    | Name | `Orcastra Dashboard` |
    | Slug | `orcastra-dashboard` |
    | Provider | `Orcastra Dashboard Provider` |
    | Launch URL | `http://<VM4_IP>:4321` |

4. Click **Create**

---

## Step 5: Create Role Groups

Orcastra uses Authentik groups for role-based access control (RBAC). Create three groups:

1. Navigate to **Directory** → **Groups**
2. Click **Create** and create each group:

    | Group Name | Dashboard Role | Access Level |
    |---|---|---|
    | `role_admin` | Admin | Full system-wide access (all clusters, users, settings) |
    | `role_partner` | Partner | Cluster owner - manages own clusters, organizations, tenants |
    | `role_tenant` | Tenant | End user - access to assigned projects only |

3. Assign `akadmin` to the `role_admin` group:
    - Click `role_admin` → **Users** tab → **Add existing user**
    - Select `akadmin` → **Add**

!!! info "User Assignment Rules"
    - Each user should belong to exactly one role group
    - If a user is in multiple role groups, the highest-privilege role takes effect

---

## Step 6: Create API Token

The API token enables the Dashboard backend to sync user groups from Authentik.

1. Navigate to **Directory** → **Tokens and App passwords**
2. Click **Create**
3. Configure:

    | Field | Value |
    |---|---|
    | Identifier | `orcastra-dashboard-api` |
    | User | `akadmin` |
    | Intent | `API Token` |
    | Expiring | Set an appropriate expiration (or leave unchecked for non-expiring) |

4. Click **Create**, then click the **copy icon** to copy the token key

!!! danger "Save This Token"
    This is your `AUTHENTIK_API_TOKEN` - required in the `.env` file on VM 4.

??? tip "Can't copy the token? (ClipboardItem error)"
    If you see "ClipboardItem is not defined":

    1. Open `chrome://flags/#unsafely-treat-insecure-origin-as-secure` in your browser
       (replace `chrome://` with `edge://` or `brave://` as appropriate)
    2. Change the setting from **Disabled** to **Enabled**
    3. Add `http://<VM1_IP>:9000` to the text field
    4. Restart the browser and retry

---

## Step 7: Note the Issuer URL

Your Authentik issuer URL is:

```
http://<VM1_IP>:9000/application/o/orcastra-dashboard/
```

!!! warning "Trailing Slash Required"
    The Issuer URL **must** end with a trailing `/`.

This is used as `AUTHENTIK_ISSUER` in the Dashboard `.env` file on VM 4.

---

## Output Summary

After completing VM 1 setup, you should have the following values saved:

| Value | Environment Variable (VM 4) | Example |
|---|---|---|
| Authentik admin password | - | *(your chosen password)* |
| Client ID | `AUTHENTIK_CLIENT_ID` | `abc123...` |
| Client Secret | `AUTHENTIK_CLIENT_SECRET` | `def456...` |
| API Token | `AUTHENTIK_API_TOKEN` | `ghi789...` |
| Issuer URL | `AUTHENTIK_ISSUER` | `http://<VM1_IP>:9000/application/o/orcastra-dashboard/` |

---

## Port Forwarding Reference

If using LXD port forwarding to access services from outside the LXD network:

1. Navigate to **Networking** → **Networks** → select your network (e.g., `lxdbr0`)
2. Click **Forwards** → **Create Forward**
3. Set **Listen Address** to the LXD host's public/reachable IP
4. Add ports:

    | Listen Port | Target IP | Target Port |
    |---|---|---|
    | 9000 | `<VM1_PRIVATE_IP>` | 9000 |

5. Click **Create**

!!! note
    Port forwarding rules for all four VMs are listed in the [Networking](../operations/networking.md) guide.

---

**Next:** [VM 2 - Vault (Secrets)](vm2-vault.md)
