# Certificate Authentication

Orcastra Mini replaces the identity provider with TLS client certificates. This page explains
how enrollment, identity, revocation, and sessions work.

## Trust-on-first-use

nginx requests a client certificate but does not reject unknown issuers
(`ssl_verify_client optional_no_ca`). Trust is decided by the application, not by a certificate
authority: a certificate is accepted only if its fingerprint is enrolled in the local identity
store with an active status. This is the same model the LXD and Incus web UIs use.

Because there is no CA check at the TLS layer, certificates can be self-signed. The first
administrator's certificate usually is; certificates issued afterward come from Vault PKI.

## Identity and role

- **Identity** is the SHA-256 fingerprint of the presented certificate, recomputed by the
  backend from the forwarded certificate. The fingerprint header from nginx is treated as
  advisory only.
- **Role** comes from the local identity store row for that fingerprint, never from the
  certificate subject. A self-signed `CN=admin` does not grant admin; only an enrolled
  `admin` row does.
- An identity whose fingerprint is unknown, pending, or inactive gets no access. There is no
  default role.

## Enrolling the first admin

The first admin is enrolled with a one-time bootstrap token. The flow is single-winner: once
an active admin exists, the bootstrap endpoint is closed.

```bash
curl -sk https://your-host.example.com:6969/api/v1/auth/cert-bootstrap \
  --cert admin.crt --key admin.key \
  -H 'Content-Type: application/json' \
  -d '{"bootstrap_token":"<BOOTSTRAP_ADMIN_TOKEN>"}'
```

!!! warning "Close the window after bootstrap"
    Blank `BOOTSTRAP_ADMIN_TOKEN` in `.env` and restart the backend once the first admin
    exists. The endpoint refuses to run when the token is unset or when an admin already
    exists, but removing the token leaves no standing secret.

The full first-run sequence is in the [Quick Start](quick-start.md#6-bootstrap-the-first-administrator).

## Issuing identities

Administrators issue Partner and Tenant certificates from **Administration -> Identities**:

1. Choose a username, a role, and a validity period.
2. The backend issues a certificate through Vault PKI and returns it as a password-protected
   `.p12`. The bundle downloads once and the import password is shown once.
3. Deliver the `.p12` and its password to the user through separate channels.
4. The user imports the `.p12` into the browser and signs in.

!!! note "Out-of-band password"
    The `.p12` is encrypted with a random one-time password delivered in a response header,
    not embedded in the file. Treat both the file and the password as secrets.

## Revocation and role changes

Revoke, re-activate, or change a role from the same Identities screen.

- Revocation is enforced at the application layer, because `optional_no_ca` does not consult a
  CRL. The identity's status is checked on every request through a Redis flag.
- A revoked or role-changed identity is rejected (or re-evaluated) on its **next request**,
  regardless of how much life remains in its session token.

!!! warning "Open consoles and terminals"
    An already-open console or terminal WebSocket re-checks revocation about every 60 seconds
    and closes when the identity is revoked. HTTP requests are re-checked on every call.

## Sessions

A verified certificate is exchanged at login for a short-lived session token (HS256), which
the rest of the application uses like any bearer token. Key points:

- The token lifetime is `LOCAL_JWT_TTL_SECONDS` (default one hour). Active revocation does not
  depend on this value; it is enforced per request.
- When a token expires, the browser re-authenticates transparently: it re-presents the
  imported certificate to nginx and a fresh token is minted with no user interaction. The user
  is only sent to the sign-in page if the certificate is missing or no longer valid.
- Tokens are namespaced (distinct issuer and audience) so they cannot be confused with
  full-version (Authentik) tokens.

## Threat-model notes

- The backend honours the forwarded `X-SSL-*` certificate headers only behind the shared
  `AUTH_PROXY_SECRET` (and the optional `TRUSTED_AUTH_PROXY_CIDRS`). The browser-facing API
  proxy strips those headers, and the backend port is not published to the host.
- The bootstrap token is compared in constant time and never logged.
- All authentication events (login, bootstrap, issue, revoke) are written to the
  [audit log](operations.md#audit-log).
