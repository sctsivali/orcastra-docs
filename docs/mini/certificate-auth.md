# Certificate Authentication

Orcastra Mini replaces the identity provider with TLS client certificates. This page explains
how sign-in, operators, roles, renewal, revocation, and sessions work, and how to recover when
no Administrator can sign in.

## Trust-on-first-use

nginx requests a client certificate but does not reject unknown issuers
(`ssl_verify_client optional_no_ca`). Trust is decided by the application, not by a certificate
authority: a certificate is accepted only if its fingerprint belongs to an active operator and
the certificate itself is active and unexpired. This is the same model the LXD and Incus web UIs
use.

Because there is no CA check at the TLS layer, certificates can be self-signed. The first
administrator's certificate usually is; certificates issued afterward come from Vault PKI.

## Identity and role

- **Identity** is the operator the certificate belongs to. The backend computes the
  certificate's SHA-256 fingerprint from the certificate nginx forwards and looks it up in the
  local identity store. An operator can hold several certificates (a second device, a renewal).
- **Email** is how organizations, integration keys and the audit log know the operator. It is
  set when the operator is created and cannot be changed later.
- **Role** (Administrator, Partner or Tenant) comes from the operator's record, never from the
  certificate subject. A self-signed `CN=admin` does not grant admin; only an Administrator
  operator does.
- A certificate that belongs to no operator, or to an operator who is revoked or deleted, gets
  no access. There is no default role.

## Signing in

The sign-in page asks the backend what the browser presented (`/api/v1/auth/cert-status`) and
shows one next step:

| What the browser presented | What the sign-in page shows |
|---|---|
| No certificate | How to import the `.p12` file (Windows, macOS, Linux or Firefox) and a **Reload** button |
| A certificate that belongs to no operator | The certificate's SHA-256 fingerprint with a copy button, and a request to contact an Administrator for a certificate issued from the Operators page |
| A certificate that belongs to no operator, while no Administrator can sign in and `BOOTSTRAP_ADMIN_TOKEN` is set | The fingerprint and the first-administrator enrollment form (see below) |
| A revoked or expired certificate, or one of a revoked or deleted operator | A notice to ask an Administrator for a new certificate, or to choose another certificate |
| A usable certificate, after a redirect from a page | Signs in on its own |
| A usable certificate, on a plain visit (for example after signing out) | "You are signed out" and a **Sign In with Certificate** button, so signing out sticks |

The browser picks a certificate when it opens a connection. To sign in with a different
certificate, close every window of the browser and open it again; nginx keeps no TLS session
cache, so the next connection presents the certificate you choose.

## Enrolling the first administrator

The first Administrator is enrolled with the one-time bootstrap token, the value set as
`BOOTSTRAP_ADMIN_TOKEN` in `.env`. Enrollment works only on a deployment that never had an
Administrator, and only while the token is set. Once any Administrator was enrolled (even one
since revoked, deleted or expired) the token no longer works; a lost Administrator is recovered
on the host with the [recovery command](#recovering-when-no-administrator-can-sign-in).

=== "On the sign-in page"

    Import the certificate into the browser and open the dashboard. The sign-in page shows the
    certificate's fingerprint and a form with **Bootstrap Token**, **Email (Optional)** and
    **Display Name (Optional)**. **Enroll as Administrator** enrolls the certificate and signs in.

=== "With curl"

    ```bash
    curl -sk https://your-host.example.com:6969/api/v1/auth/cert-bootstrap \
      --cert admin.crt --key admin.key \
      -H 'Content-Type: application/json' \
      -d '{"bootstrap_token":"<BOOTSTRAP_ADMIN_TOKEN>","email":"admin@example.com"}'
    ```

    `email` and `display_name` are optional fields of the JSON body.

Without an email, the Administrator gets `<label>@users.orcastra.internal`, where the label is
the first part of the certificate's common name (`admin@users.orcastra.internal` for
`CN=admin`). Choose the email now: it cannot be changed later.

| Response | Meaning |
|---|---|
| `200` | Enrolled; the response carries a session token |
| `401` | No trusted client certificate reached the backend (check `AUTH_PROXY_SECRET`) |
| `403` | The bootstrap token is wrong, or `BOOTSTRAP_ADMIN_TOKEN` is not set |
| `409` | An Administrator already exists, or the certificate or email is already enrolled |
| `415` or `422` | The request was not sent as `application/json`, or the token (16 to 256 characters) or email is malformed |
| `429` | More than 5 attempts from one address in 10 minutes |

!!! warning "Close the window after bootstrap"
    Blank `BOOTSTRAP_ADMIN_TOKEN` in `.env` and recreate the backend once the first admin
    exists. While the token stays set and an Administrator exists, Host Health reports the
    **Certificate Sign-In** check as degraded.

The full first-run sequence is in the [Quick Start](quick-start.md#7-bootstrap-the-first-administrator).

## The Operators page

**Administration > Operators** lists operators with their role, organization, status,
certificate expiry (with **Expiring soon** inside the warning window) and last sign-in.
Administrators see every operator. Partners see the Tenants of the organizations they run.
Expanding a row lists the operator's certificates: fingerprint, status (**Active**, **Revoked**
or **Replaced by a renewal**), expiry, last use, and the certificate a renewal replaced.

### Issuing a certificate

**Issue Certificate** creates an operator and their first certificate:

1. Enter the **Email** (it cannot be changed later), an optional **Display Name**, the **Role**,
   an optional **Valid For** in days, and an **Organization** (optional for Administrators,
   required for Partners). The operator joins the organization as a member. A Tenant issued for
   an organization belongs to it, and that organization's Partners manage it.
2. The backend issues the certificate from Vault PKI (role `VAULT_PKI_ROLE`) and returns it as a
   password-protected `.p12`. The validity is the requested number of days, capped at
   `CLIENT_CERT_TTL_DAYS`.
3. The dialog shows the import password and a download button for the `.p12`. Both are shown
   once and neither is stored on the server; closing the dialog without saving both means
   issuing again.
4. Deliver the file and the password to the person through different channels. They import the
   `.p12` into the browser or OS keychain and sign in.

An email that already belongs to an operator is refused. An email that belonged to a deleted
operator is refused too: restore that operator instead.

!!! note "Vault must be unsealed"
    Every certificate (issue, reissue, renewal, recovery) comes from Vault. While Vault is
    sealed, issuing fails with `503` and a pointer to Host Health.

### Actions

The actions menu on each row offers what the signed-in operator may do. Actions are disabled
in read-only mode.

| Action | Available for | Effect |
|---|---|---|
| **Issue Another Certificate** | Active operators | Issues an additional certificate (a new device, a lost one replaced). The other certificates keep working. |
| **Make Administrator / Partner / Tenant** | Active operators, Administrators only | Changes the role. Every session of the operator ends on the next request. A promotion (Tenant to Partner or Administrator, Partner to Administrator) also revokes every certificate the operator holds, because whoever issued them holds their keys too: issue a new certificate after the change and hand it to the operator. A demotion keeps the certificates; the operator signs in again with the lower role. |
| **Revoke** | Active operators | Every session ends on the next request and open terminals close within a minute. The certificates are kept as they are. |
| **Reactivate** | Revoked operators | The operator can sign in again with any certificate that is still active. Old sessions do not come back. A Partner cannot reactivate an operator an Administrator revoked. |
| **Delete** | Operators not deleted | A tombstone: every certificate is revoked, every session ends, and the email stays reserved so nobody else inherits the operator's access. You cannot delete yourself. |
| **Restore** | Deleted operators | The operator is active again with their memberships. The old certificates stay revoked: issue a new one. A Partner cannot restore an operator an Administrator deleted. |
| **Revoke Certificate** (in the expanded row) | Active certificates | Sessions signed in with that certificate end on the next request. The operator's other certificates keep working. |

### Last-administrator guard

The Operators page refuses (`409`) a change that would leave no Administrator who can sign in
(an active Administrator with an active, unexpired certificate):

- revoking, demoting or deleting the last such Administrator;
- revoking the last usable certificate of the only such Administrator.

Issue a second Administrator certificate early. Host Health advises it while only one
Administrator can sign in.

### What a Partner can do

A Partner runs an organization when it created the organization or is a member with the
Partner role. On the Operators page a Partner:

- issues Tenant certificates only, and must choose an organization it runs;
- cannot issue a certificate for an email that already has access outside the organizations it
  runs (a membership elsewhere, an organization or a cluster registration), and cannot issue
  another certificate for a Tenant that also belongs to another organization: an Administrator
  does that, because access follows the email;
- sees and manages only Tenants that belong to organizations it runs (issue another certificate,
  revoke certificates, revoke, reactivate, delete, restore), except that it cannot undo a revoke
  or delete an Administrator made;
- cannot change a role, and cannot see or touch Partners, Administrators, or Tenants of other
  organizations.

## Renewing a certificate

Every operator (Administrator, Partner or Tenant) can renew the certificate they signed in with
from **My Certificate** in the user menu. The page shows the email, role, certificate common
name, expiry with the days left, fingerprint, and the number of other active certificates.

- **Window.** **Renew Certificate** appears from 30 days before the certificate expires
  (`CERT_RENEW_WINDOW_DAYS`). Before that the page shows the date renewal opens.
- **Once per certificate.** A certificate that was already renewed cannot be renewed again;
  import the new one.
- **Lifetime.** The new certificate lasts as long as the one it replaces (a 30-day certificate
  renews for 30 days), never longer than `CLIENT_CERT_TTL_DAYS`.
- **Result.** A new `.p12` and its import password, shown once, as on the Operators page.
  Import the file, close every window of the browser, open it again, and choose the new
  certificate.
- **The old certificate** keeps working until the first sign-in with the new one, or until
  `CERT_RENEW_OVERLAP_HOURS` (default 24) after the renewal if the new one is never used. It is
  then shown as **Replaced by a renewal** and can no longer sign in.
- **Turning it off.** With `CERT_SELF_RENEW_ENABLED=false` the button is not offered and the
  renewal endpoint answers `403`. Administrators and Partners then use **Issue Another
  Certificate** on the Operators page.

An expired certificate cannot sign in, so it cannot be renewed: ask an Administrator (or the
organization's Partner) for a new certificate.

## Expiry warnings

- While the signed-in certificate expires within `CERT_EXPIRY_WARNING_DAYS` (default 14 days),
  every page shows a banner with the expiry date and days left, and a **Renew it now** link when
  renewal is available (otherwise "Ask an Administrator for a new certificate").
- The Operators page marks those operators **Expiring soon** and can filter by it.
- Host Health's **Certificate Sign-In** check counts **Certificates Expiring Soon**.

## Sessions

A verified certificate is exchanged at sign-in (`/api/v1/auth/cert-login`) for a short-lived
session token, which the rest of the application uses like any bearer token. Key points:

- **Bound to the certificate.** The token carries the SHA-256 thumbprint of the certificate it
  was issued for (`cnf.x5t#S256`, RFC 8705). Every request must arrive through nginx presenting
  that same certificate, so a copied token is useless without the certificate's private key.
- **Lifetime.** `LOCAL_JWT_TTL_SECONDS` (default 900 seconds). The browser renews the session by
  signing in again with the same certificate, without user interaction. If the certificate is
  no longer accepted, or the browser now presents another operator's certificate, the session
  ends and the operator is asked to sign in again.
- **Namespaced.** Tokens are signed (HS256) with `LOCAL_JWT_SECRET` and carry their own issuer
  (`orcastra-mini`) and audience (`orcastra-mini-dashboard`), so they cannot be confused with an
  identity provider's tokens.
- **Audited.** A sign-in that cannot be written to the audit log does not hand out a session.

## Revocation

Revocation is enforced at the application layer, because `optional_no_ca` does not consult a
CRL. Every authenticated request checks, in order: the token's signature and expiry, that the
presented certificate is the one the token names, that the operator is active, still holds the
token's role and has not been revoked, deleted or changed since the token was issued, and that
the certificate is active and unexpired.

- Revoking or deleting an operator, or changing a role, refuses every session token issued
  before that moment, from the next request on.
- Revoking one certificate refuses the sessions signed in with it.
- Each backend worker caches an operator's record for up to two seconds, so a change made on one
  worker reaches the others within two seconds.
- If the identity store cannot be read, requests are refused (`503`), never admitted.

!!! warning "Open consoles and terminals"
    An open console or terminal WebSocket re-checks the operator about every 60 seconds and
    closes when the operator or the certificate is revoked, the role changed, or the operator was
    deleted. An identity-store outage does not close open sockets; the next check retries.

## Recovering when no Administrator can sign in

When every Administrator certificate is lost, revoked or expired, Host Health reports
**Certificate Sign-In** as down. Someone with root on the host issues a new Administrator
certificate from inside the backend container:

```bash
docker exec -it orcastra-mini-backend python -m app.editions.mini.cli recover-admin --email you@example.org
```

Replace `orcastra-mini` with your `CONTAINER_PREFIX` if you changed it.

- With an email that belongs to an existing operator, that operator becomes an active
  Administrator, their older sessions are refused, and a new certificate is added. With a new
  email, a new Administrator operator is created.
- The `.p12` is written to `/app/config` in the container, which is the deployment's `config/`
  directory on the host (`/opt/orcastra-mini/config/` for an installer deployment), as
  `recovered-admin-<UTC timestamp>.p12` with mode `600`. The import password is printed once.
  The backend runs as uid `1000`, so `config/` must belong to that uid; the installer sets it
  (see [Operations](operations.md#directories-the-backend-writes)).
- Import it into your browser, sign in, then delete the file.
- The command refuses (exit code 2) while an Administrator can still sign in. `--force` issues
  a certificate anyway, and `--out-dir` writes the file elsewhere inside the container.
- Vault must be unsealed. Without an Administrator the dashboard cannot unseal it, so unseal
  from the host first (see [Operations](operations.md#vault-unseal-after-a-restart)).
- The recovery is written to the audit log as `identity.recover_admin` with severity
  `critical`.

## Threat-model notes

- The backend honours the forwarded `X-SSL-*` certificate headers only when they arrive with the
  shared `AUTH_PROXY_SECRET` (and, optionally, from inside `TRUSTED_AUTH_PROXY_CIDRS`). nginx
  overwrites those headers from the TLS layer on every request, the frontend drops any copy a
  browser sends, and the backend port is not published to the host.
- The bootstrap token is compared in constant time and never logged.
- The server keeps a certificate's fingerprint, serial and expiry, never its private key or the
  `.p12` password.
- Security events (sign-in, bootstrap, issue, renew, revoke, role change, delete, restore,
  recovery, audit export and prune) are written to the [audit log](operations.md#audit-log)
  synchronously, and the change does not complete without its audit row.
