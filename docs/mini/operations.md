# Operations & Troubleshooting

Day-2 tasks for a running Orcastra Mini host.

## Vault unseal after a restart

Vault uses manual unseal by design. After any restart of the Vault container (host reboot,
`docker restart`, or a recreate), it comes up sealed and must be unsealed before certificate
issuance works. Login keeps working while Vault is sealed; only identity issuance and cluster
certificate features are unavailable.

```bash
docker compose -f docker-compose.mini.yml exec vault \
  vault operator unseal <unseal-key>
# repeat with two more distinct keys
```

!!! tip "Confirm state"
    `docker compose -f docker-compose.mini.yml exec vault vault status` should show
    `Sealed: false`.

## Managing identities

From **Administration -> Identities**:

- **Issue** a Partner or Tenant certificate (downloads a one-time, password-protected `.p12`).
- **Revoke** or **re-activate** an identity; revocation takes effect on the next request.
- **Change role**; the new role applies on the identity's next request (its session is flagged
  in Redis to force re-evaluation).

See [Certificate Authentication](certificate-auth.md) for the full lifecycle.

## Audit log

The audit trail is an append-only, hash-chained PostgreSQL table. Use
**Administration -> Audit Log**:

- Filter by date range and sort columns; results are paginated.
- **Verify integrity** recomputes the chain. It detects in-place edits and reordering, and
  also reports tail truncation by comparing the current row count and head against a stored
  high-water mark.

!!! note "External anchor"
    The chain proves rows were not altered, but a determined database-level attacker who can
    edit both the table and the anchor is outside its guarantee. For stronger assurance,
    periodically export the latest head hash and row count to an append-only, off-host sink.

## Container images

Prebuilt images are published to Docker Hub at `svlct/orcastra-dashboard-mini`, with
component-prefixed tags:

| Tag | Component |
|---|---|
| `backend-1.0.0-RC1` | FastAPI backend |
| `frontend-1.0.0-RC1` | Next.js frontend |
| `backend-rc`, `frontend-rc` | Always the latest release candidate |

```bash
docker pull svlct/orcastra-dashboard-mini:backend-1.0.0-RC1
docker pull svlct/orcastra-dashboard-mini:frontend-1.0.0-RC1
```

To run these instead of building locally, set each service's `image:` in
`docker-compose.mini.yml` to the corresponding tag and run `docker compose -f
docker-compose.mini.yml up -d`. Both images read all secrets from environment variables at
runtime; the frontend swaps its API URL placeholder on start, so the same image works on any
host.

## Troubleshooting

| Symptom | Cause | Resolution |
|---|---|---|
| Certificate issuance fails or returns 503 | Vault is sealed | Unseal Vault (see above). |
| Issuance fails with "notAfter beyond the expiration of the CA" | CA TTL equals the leaf TTL | Provision the CA at a long TTL (for example 10 years) and the role at a shorter maximum (for example 1 year). |
| `cert-bootstrap` returns 403 | `BOOTSTRAP_ADMIN_TOKEN` unset, an admin already exists, or the token is wrong | Set the token before bootstrap; the window is one-shot. |
| `cert-bootstrap` returns 401 | No trusted client certificate reached the backend | Present the certificate (`--cert`/`--key`) and confirm `AUTH_PROXY_SECRET` matches between nginx and the backend. |
| API calls return 401 a while after login | Session token expired | The browser re-authenticates with the certificate automatically; if it does not, confirm the certificate is still imported and valid. |
| The chosen public port conflicts | Another service uses it | Change `HTTPS_PORT` (and the three URL values), then recreate. |

## Backups

The PostgreSQL volume holds application state and the audit log. Vault holds the PKI and
secrets. Back up both volumes (and the Vault unseal keys, stored offline) as part of routine
operations.
