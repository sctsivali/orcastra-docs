# Build Your First Add-on

**From nothing to an add-on that authenticates, reads inventory, and verifies a signed webhook. Roughly twenty minutes, and every command below is copy-pasteable.**

---

You need the partner role in an Orcastra deployment, an organization with at least one cluster bound to it, and somewhere to run a small HTTP server that Orcastra can reach.

## 1. Publish the add-on

Publishing describes what your add-on is and what it asks for. It grants nothing on its own.

Open **Extensions** in the sidebar and choose **Publish Add-on**. Two ways in, and they produce
the same thing:

=== "Form"

    Fill in the slug, name, publisher and version, tick the capabilities you need, and choose the
    scope shape. Use this the first time, when you would rather not learn the field names.

=== "Manifest file"

    Paste your manifest, or choose a `.json` file. Use this once the manifest lives in version
    control, which is where it belongs, and whenever it declares settings fields or webhook
    defaults: the form does not build those.

=== "API"

    ```bash
    curl -sS -X POST "https://<YOUR_ORCASTRA_API>/api/v1/extensions" \
      -H "Authorization: Bearer <YOUR_DASHBOARD_TOKEN>" \
      -H "Content-Type: application/json" \
      -d @manifest.json
    ```

    `<YOUR_DASHBOARD_TOKEN>` is the bearer token your dashboard session already uses. Open the
    browser's developer tools, make any request in the dashboard, and copy the `Authorization`
    header. It is a session token, so it expires; the UI above needs no token at all, which is why
    it is listed first.

A complete manifest, with every field explained, is on [the manifest reference](manifest.md):

```json
{
  "slug": "acme-backup",
  "name": "Acme Backup",
  "publisher": "Acme Ltd",
  "version": "1.0.0",
  "description": "Snapshots instances on a schedule",
  "capabilities": ["inventory.read", "lxd.proxy"],
  "scope": {"permission": "write", "clusters": "selected", "projects": "selected"},
  "settings": [
    {"key": "retention_days", "type": "integer", "label": "Retention", "min": 1, "max": 90, "default": 7},
    {"key": "api_token", "type": "secret", "label": "Acme API token", "required": true}
  ]
}
```

Ask for the narrowest set of capabilities that does the job. Every one of them appears on the consent screen, and an operator deciding whether to trust you reads that list.

!!! warning "What you publish is a draft, and a draft is invisible"
    A newly published add-on has status `draft`. Only you can see it. Nobody can install it, and
    an operator opening **Extensions** will not be told it exists.

    It appears under **Your Drafts** on that page with a **Publish** button next to it. Press that
    and it becomes installable. Through the API the same step is
    `PATCH /api/v1/extensions/{slug}` with `{"status": "published"}`.

    This trips people up because nothing errors: you publish, the operator sees an empty
    catalogue, and neither of you is told why.

!!! warning "The manifest you publish is the manifest that is enforced"
    The document is submitted, not fetched from a URL you host. Orcastra stores a hash of it, and an operator's consent is recorded against that hash. Changing what your add-on asks for means publishing a new version, covered in [versioning](versioning.md), which existing installations must approve before they move to it.

## 2. Have an operator install it

Installing is done in the dashboard, under **Extensions**, or from **Settings** then **Extensions**. The operator chooses the organization, reads what is being granted, and confirms.

The consent screen shows the clusters your add-on will actually reach, which is the intersection of what you asked for and what that operator holds. It can be shorter than you expect, and the screen says which clusters were refused and why. That is normal: your add-on is never given more access than the person installing it has.

They will hand you back two secrets and an installation id. If they forget the id, your own
credential can tell you: `GET /extensions/self` returns it as `installation_id`, and steps 5 and 6
need it.

## 3. Save the credential

The install shows two values, once, and neither can be read again.

```bash
# .env, written wherever your add-on reads its configuration from
ORCASTRA_API_URL=https://<YOUR_ORCASTRA_API>
ORCASTRA_API_KEY_ID=oak_...
ORCASTRA_API_KEY_SECRET=oas_...
ORCASTRA_WEBHOOK_SECRET=whsec_...
```

The API key authenticates your add-on. The webhook secret verifies that a delivery came from Orcastra. They are separate values, so a system that only needs to check signatures never has to hold a credential that can act.

!!! danger "If you lose one, rotate it"
    There is no way to read either value back. Rotating issues a new credential and, if you ask for a grace window, leaves the old one working for a while so you can deploy without downtime.

!!! tip "Use the API origin, not the dashboard origin"
    `ORCASTRA_API_URL` is the backend, not the address you open the dashboard on. Every integration calling through the dashboard shares one rate-limit bucket with every browser user, so an add-on that works in testing will be throttled in production.

## 4. Make the first authenticated call

Both headers are required together. Neither one alone authenticates anything.

```bash
curl -sS "$ORCASTRA_API_URL/api/v1/extensions/self" \
  -H "X-API-Key-ID: $ORCASTRA_API_KEY_ID" \
  -H "X-API-Key-Secret: $ORCASTRA_API_KEY_SECRET"
```

A working credential answers with what your installation currently holds:

```json
{
  "installed": true,
  "installation_id": 7,
  "status": "active",
  "extension": {"slug": "acme-backup", "name": "Acme Backup", "version": "1.0.0"},
  "organization": {"id": 3, "slug": "acme"},
  "granted_capabilities": ["inventory.read", "lxd.proxy"],
  "granted_scope": [{"cluster_id": "nuc2", "permissions": ["write"], "projects": ["default"]}],
  "settings": {"retention_days": 7, "api_token": "..."},
  "credential_generation": 1
}
```

Two different failures, and they mean different things. A credential that is wrong, expired or revoked answers `401`. Omitting either header entirely answers `422`, because the headers are declared as required, so a client that sends only the key id gets a validation error rather than an authentication one.

Poll this endpoint rather than caching what you were told at install: it is how you notice that an operator narrowed your reach, suspended you, or rotated your credential.

Now read something real:

```bash
curl -sS "$ORCASTRA_API_URL/api/v1/integrations/instances" \
  -H "X-API-Key-ID: $ORCASTRA_API_KEY_ID" \
  -H "X-API-Key-Secret: $ORCASTRA_API_KEY_SECRET"
```

## 5. Subscribe to an event

Deliveries go to an endpoint you register. It has to be reachable from the Orcastra deployment and it has to be `https`.

```bash
curl -sS -X POST \
  "$ORCASTRA_API_URL/api/v1/extensions/installations/$INSTALLATION_ID/webhooks" \
  -H "Authorization: Bearer <YOUR_DASHBOARD_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "endpoint_url": "https://hooks.acme.example/orcastra",
    "event_types": ["orcastra.instance.created.v1", "orcastra.instance.deleted.v1"]
  }'
```

Private, loopback, link-local and carrier-grade-NAT addresses are refused, and so is anything that resolves to one. The refusal names a category rather than an address, so you will read `host resolves to an internal address` rather than which address it was.

A new subscription is not live yet. It answers `pending_verification` until the endpoint proves it wants the traffic:

```bash
curl -sS -X POST \
  "$ORCASTRA_API_URL/api/v1/extensions/installations/$INSTALLATION_ID/webhooks/$SUBSCRIPTION_ID/verify" \
  -H "Authorization: Bearer <YOUR_DASHBOARD_TOKEN>"
```

That sends one signed ping. Answer it `2xx` and the subscription goes active. This step exists so nobody can register somebody else's URL and aim traffic at them.

## 6. Verify the signature

This is the step that is most often got wrong, so the rules come first.

Verify over the **raw request body**, before anything parses it. Compare with a **constant-time** function. Reject a timestamp outside **five minutes**.

Every delivery carries these headers:

| Header | Meaning |
|---|---|
| `X-Orcastra-Signature` | Space-separated list of `v1,k=<fingerprint>:<hex>` entries |
| `X-Orcastra-Timestamp` | Unix seconds, and part of what is signed |
| `X-Orcastra-Event-Id` | Stable across every attempt. Your idempotency key |
| `X-Orcastra-Delivery-Id` | Unique per attempt |
| `X-Orcastra-Event-Type` | The event name |

The signed material is the literal string `v1.`, the timestamp, a full stop, then the raw body bytes.

=== "Python"

    ```python
    import hashlib
    import hmac
    import time

    def fingerprint(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()[:12]

    def verify(raw_body: bytes, headers, secret: str) -> bool:
        timestamp = int(headers["X-Orcastra-Timestamp"])
        if abs(time.time() - timestamp) > 300:
            return False
        digest = hmac.new(
            secret.encode(),
            f"v1.{timestamp}.".encode() + raw_body,
            hashlib.sha256,
        ).hexdigest()
        expected = f"v1,k={fingerprint(secret)}:{digest}"
        return any(
            hmac.compare_digest(entry, expected)
            for entry in headers["X-Orcastra-Signature"].split()
        )
    ```

=== "Node"

    ```javascript
    const crypto = require("node:crypto");

    const fingerprint = (secret) =>
      crypto.createHash("sha256").update(secret).digest("hex").slice(0, 12);

    function verify(rawBody, headers, secret) {
      const timestamp = Number(headers["x-orcastra-timestamp"]);
      if (Math.abs(Date.now() / 1000 - timestamp) > 300) return false;

      const digest = crypto
        .createHmac("sha256", secret)
        .update(Buffer.concat([Buffer.from(`v1.${timestamp}.`), rawBody]))
        .digest("hex");
      const expected = `v1,k=${fingerprint(secret)}:${digest}`;

      return headers["x-orcastra-signature"]
        .split(" ")
        .some((entry) =>
          entry.length === expected.length &&
          crypto.timingSafeEqual(Buffer.from(entry), Buffer.from(expected)),
        );
    }
    ```

!!! danger "Do not verify against re-serialised JSON"
    Parsing the body and serialising it again produces different bytes in almost every language's defaults, and the signature will not match. Frameworks that hand you a parsed object often discard the raw body, so capture it before your JSON middleware runs.

!!! note "Why the header is a list"
    During a secret rotation both signatures are sent, so you can switch when you are ready rather than when we are. Accept the delivery if any entry verifies.

## 7. Handle a retry

Delivery is at-least-once. You will see the same event more than once, and you must dedupe on `X-Orcastra-Event-Id`.

| Behaviour | Value |
|---|---|
| Attempts | 5 |
| Backoff | roughly 10s, 1m, 5m, 15m, 30m, each with jitter |
| Given up after | about 50 minutes, and the delivery is marked dead |
| Endpoint suspended after | 20 consecutive failures |
| Expected response time | under 10 seconds |

Answer `2xx` quickly and do your work afterwards. A slow `200` is worse for both of us than a fast `202`, because the sender holds a connection for as long as you take.

If your endpoint is suspended, fix it and verify the subscription again.

!!! note "A withheld delivery is not a failure"
    A delivery marked `dropped` was never sent, because the installation stopped reaching that cluster between the event happening and the delivery going out. That is the revocation working, not an outage.

## Where to go next

- [Capabilities](capabilities.md) for what each one permits and which are marked
- [Webhooks](webhooks.md) for the full event catalogue and payload shape
- [Security model](security.md) for the boundary, including its known limits
