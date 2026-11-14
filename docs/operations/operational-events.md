# Operational Events

From 1.0.0 the dashboard reports faults in what it depends on as operational events: one
record when a fault starts and one when it clears, each with a plain summary, the fix, and a link
to the matching section below. They are written where operators already look, so a deployment
does not depend on anyone else's alerting to learn about them.

## Where to find them

| Place | What it shows | Who sees it |
|---|---|---|
| Console, task panel, **Log** tab | One row per event, with its severity and the fix in the message | Administrators see every event. A partner sees the events of the clusters it holds, whatever project bound it has. Tenants see none |
| OpenSearch, `orcastra-audit-*` | The full record, kept with the audit trail (3 years) | Whoever can read the deployment's own OpenSearch |
| Inventory, Warnings, **Host Health** | The current state of Vault (with the token) and of the secrets | Administrators |
| Notice at the top of every page | A refused Vault token, or secrets still at the template placeholder | Every role sees that Vault is unavailable; Administrators also see why and the fix |

In OpenSearch Dashboards, create an index pattern for `orcastra-audit-*` and filter on
`category: operations` (or `action: system.*`). The fields that matter:

| Field | Meaning |
|---|---|
| `action` | `system.<event code>`, for example `system.vault.token_refused` |
| `details.event_code` | The event code |
| `details.state` | `firing` when the fault starts, `resolved` when it clears, `recorded` for an event that is history only |
| `details.severity` | `critical`, `warning` or `info` |
| `details.summary` | What happened, in plain words |
| `details.fix` | What to do (firing events only) |
| `details.docs_url` | The section of this page for the event |
| `target.host` | The cluster, for a cluster event; empty for a deployment event |

A change is reported once even with several backend workers. Each transition has its own
`event_id`, which is also the OpenSearch document id, so a duplicate never becomes a second
document.

## Vault token refused

`vault.token_refused`, critical, deployment.

Vault answers 403 to every request the dashboard makes with `VAULT_TOKEN`: the token expired or was
revoked. Vault itself may be unsealed and healthy. Cluster access, certificates and integration API
keys are unavailable until the token is replaced; API key requests answer 503
`api_key_store_unavailable`, not 401, so integrations do not mistake it for a revoked key.

To fix it, on the Vault host, with a token allowed to create tokens:

```bash
vault token create -orphan -policy=orcastra-policy -period=720h -display-name=orcastra-dashboard
```

Put the new token in `VAULT_TOKEN` in the dashboard's `.env` and recreate the backend container
(`docker compose up -d --force-recreate backend`; a plain restart does not read `.env` again). The
dashboard renews a periodic token itself from then on. The event resolves on its own once Vault
accepts the token.

If no working admin token is left, see [Regenerate the Vault root token](../deployment/vm2-vault.md#regenerate-the-vault-root-token).

## Vault token expiring

`vault.token_expiring`, warning, deployment.

The dashboard's token expires within a week and cannot be renewed past that date: it is not
renewable, it reached its maximum lifetime (a token created with `-ttl=0` gets Vault's default of
768 hours), or its policy does not allow `renew-self`. The record's `details.why` says which, and
`details.expire_time` when. Replace it with a periodic token as in
[Vault token refused](#vault-token-refused) before that date.

## Vault token renewal failed

`vault.token_renewal_failed`, warning, deployment.

The dashboard tried to renew its token and Vault did not answer, or answered an error
(`details.reason`). It retries every five minutes and resolves the event on the next success. Check
that Vault is reachable from the backend container and that the token still has Vault's `default`
policy, which grants `renew-self` (it has unless the token was created with `-no-default-policy`).

## Vault token renewed

`vault.token_renewed`, info, deployment, history only.

The dashboard renewed its token; `details.lease` is how long it lasts now. With a 30-day periodic
token this happens about every 15 days. Nothing to do.

## Cluster certificate expiring

`cluster.certificate_expiring`, warning within 14 days, critical within 3, cluster.

The client certificate the dashboard presents to the cluster expires on `details.not_after`. Once it
lapses the cluster refuses the dashboard and shows Pending Trust. Renew it from Inventory, Clusters,
Certificates before then. The check runs every six hours and is skipped while Vault cannot serve the
dashboard, so a Vault outage never reads as expired certificates.

## Cluster certificate expired

`cluster.certificate_expired`, critical, cluster.

The certificate has lapsed and the cluster refuses the dashboard. Regenerate it from Inventory,
Clusters, Certificates, then add the new certificate to the cluster's trust store on one of its
hosts, and run Check Authentication. The event resolves once the certificate is current.

## Secrets still at the template placeholder

Not an event: Host Health's **Secrets and Keys** check, critical, with a notice for Administrators on
every page. It names each variable that still holds the template's `replace_me` value or is shorter
than 32 characters (`SECRET_KEY`, `NEXTAUTH_SECRET`, `REDIS_ENCRYPTION_KEY`, the database password).
The values are never shown. Generate each one:

```bash
openssl rand -hex 32       # SECRET_KEY, POSTGRES_PASSWORD
openssl rand -base64 32    # NEXTAUTH_SECRET
```

Set them in `.env` and recreate the containers. A new `SECRET_KEY` invalidates every integration API
key, so issue new keys to the integrations afterwards.
