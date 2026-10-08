# Integration Keys and Organization Ownership

**Every OrcaHub organization synced into Orcastra CMP is owned by exactly one integration key. Only that key can keep it in sync, so rotate a key's secret instead of replacing the key, and move an organization to another key before you end the one that owns it.**

An OrcaHub connection authenticates to Orcastra CMP with an integration key: a public key ID (`oak_...`) and a secret (`oas_...`). The key ID is what Orcastra records as the owner of every organization that connection syncs. This page explains what ownership controls, how to rotate a secret without breaking it, how an administrator moves an organization to another key, and how to recover an organization whose owner key stopped working.

---

## How ownership works

An organization is claimed by the first key that syncs it, provisions tenant access for it, or pulls it with **Refresh organizations**. From then on, the owner key is the only key that may:

- sync the organization (name, slug and description),
- update its members and their roles,
- write its tenant access policies, and
- provision tenant access for it when OrcaHub completes an order.

A push from any other key leaves the organization untouched, and provisioning answers `409`. An organization with no owner can be claimed by any key that syncs or provisions it.

There is no "release". An owner is replaced by another key, never cleared, because an organization without an owner is open to whichever key names it next.

!!! note "Add-on credentials"
    An add-on granted the `org.sync` capability can own organizations too. When its installation rotates the credential, the organizations move to the new credential in the same step. Uninstalling the add-on is refused while its credential owns organizations; suspend the add-on to stop it at once.

---

## See which key owns what

In **Settings > Integrations**:

- Each API key card says how many organizations the key owns and lists their slugs. A partner sees only the organizations it belongs to; the rest are counted as "you cannot see".
- An owner key inside its expiry warning window shows **Owner Key Expiring** and the date to transfer its organizations by.
- The **Synced Organizations** panel lists every synced organization with its owner key and the owner's state.

| Owner state | Meaning |
|---|---|
| Active | The owner key works. |
| Expiring | The owner key works and expires within 14 days. |
| Expired, Revoked, Inactive | The owner key no longer works. The organization is frozen until it is transferred. |
| Missing | The owner key no longer exists. The organization is frozen until it is transferred. |
| Unknown | The owner key's record could not be read (for example, Vault is unreachable). Retry before acting. |
| Unclaimed | No key owns the organization yet. |

Administrators also see a callout counting the organizations whose owner no longer works.

The same information is in the API: `GET /api/v1/integrations/organizations` returns `owner_key_name`, `owner_key_state`, `owner_key_expires_at`, `owner_key_expiry_state` and `cluster_ids` for each organization, and `GET /api/v1/integrations/api-keys` returns `owned_organization_count`, `owned_organization_slugs` and `owned_organization_hidden_count` for each key.

---

## Rotate a secret without changing the key ID

Rotate the secret whenever a key's secret may have leaked, or on your regular schedule. The key ID stays, so every organization the key owns keeps syncing once OrcaHub has the new secret.

1. In **Settings > Integrations**, choose **Rotate Secret** on the key's card. The control appears on active keys that are not an add-on's credential.
2. Copy the new secret from the dialog. It is shown once.
3. Update the OrcaHub connection with the new secret.

The previous secret stops working immediately, on every backend worker. Until OrcaHub has the new secret, its syncs are refused with `401`.

Rotation also clears the key's stored callback URL. The callbacks Orcastra sends to OrcaHub carry the key's secret, and a URL set by whoever held a leaked secret would otherwise receive the new one. The OrcaHub connection stores its URL again on its first sync with the new secret. Until then, Orcastra sends no callbacks for the key, and **Refresh organizations** answers `400` "No callback URL configured".

Who may rotate:

- An administrator may rotate any key.
- A partner who manages the key may rotate it when the key owns no OrcaHub organization, or when the partner runs at least one of the organizations it owns (created it, or is one of its partners). This applies to the key's creator too.
- A partner who did not create the key also needs every cluster the key grants to be within their access and, on a cluster another organization also holds, every project it names to be one they can see.

Anyone else gets `403` (or `404` for a key they cannot manage), and a refused rotation is recorded in the audit log.

API equivalent:

```bash
curl -X POST https://<dashboard>/api/v1/integrations/api-keys/<key_id>/rotate-secret \
  -H "Authorization: Bearer <token>"
```

The response carries `api_key_id` (unchanged) and `api_key_secret` (new). A revoked, inactive or expired key answers `409`; create a new key instead. An add-on credential answers `409`; rotate it from its installation.

A `503` answer says which of these happened:

- The callback URL could not be cleared, or the organizations the key owns could not be checked: nothing changed, and the old secret still works. Try again.
- The rotation did not confirm, or could not confirm the callback URL was cleared afterwards: a new secret may already be stored, and it was not shown. Rotate again before using the key.

!!! warning "Do not replace a key to rotate it"
    Creating a new key for the same OrcaHub connection gives it a new key ID. The organizations stay owned by the old key, so every sync from the new key is refused for them until an administrator transfers them.

---

## Transfer an organization to another key

Administrators move an organization to another owner key from **Settings > Integrations > Synced Organizations**:

1. Open the row's actions and choose **Transfer Owner**.
2. Pick the new owner key. Only keys that could own the organization are offered: active OrcaHub keys holding `inventory.read` and `org.sync` that are not an add-on credential and not the current owner.
3. Enter the reason. It is kept in the audit record.
4. Type the organization's slug to confirm, then choose **Transfer Owner**.

The new key owns the organization at once. OrcaHub confirms on that connection's next sync, within about 5 minutes on OrcaHub's default schedule. The dialog warns when the new key expires soon, or when its access does not include a cluster the organization is bound to: provisioning tenant access on such a cluster is refused until the key's access includes it.

API equivalent:

```bash
curl -X PUT https://<dashboard>/api/v1/integrations/organizations/<org_id>/owner-key \
  -H "Authorization: Bearer <admin token>" \
  -H "Content-Type: application/json" \
  -d '{"api_key_id": "oak_...", "reason": "The old connection key was revoked"}'
```

A move to the key that already owns the organization answers `200` with `"changed": false` and changes nothing. Otherwise the response names the previous owner and its state, the new owner, the clusters its access does not include (`uncovered_cluster_ids`), and how many organizations the new owner now claims.

| Status | `error` | Cause |
|---|---|---|
| 404 | | No organization with that ID. |
| 409 | `organization_not_synced` | The organization was created in Orcastra, not synced from OrcaHub, so no key owns it. |
| 409 | `target_key_missing` | No API key with that ID exists. |
| 409 | `target_key_is_add_on` | The key is an add-on credential. |
| 409 | `target_key_revoked`, `target_key_expired`, `target_key_inactive` | The key no longer works. |
| 409 | `target_key_cannot_sync` | The key lacks `inventory.read` or `org.sync`. OrcaHub's sync needs both. |
| 409 | `target_key_not_orcahub` | The key is not an OrcaHub connection. |
| 422 | | The body is missing the key ID or the reason, or the key ID is malformed. |
| 503 | | The key store could not be read, so nothing changed. |

Only administrators may transfer. Partners and tenants get `403`, and so does every caller while the dashboard is in read-only mode. Each transfer, and each refused transfer, is recorded as `integration.organization.owner.transfer` in the audit log.

---

## What revoke, delete and uninstall refuse

Revoking or deleting a key that owns an active OrcaHub organization, or withdrawing its `inventory.read` or `org.sync` capability, answers `409` with `error: key_owns_organizations`. OrcaHub's sync needs both capabilities. This applies to every role, administrators included. The response says which organizations the key owns and what to do instead:

- transfer the organizations to another active key, or delete them (administrators), then end the key; or
- to stop a leaked secret right now, rotate the secret: the key ID and its organizations stay.

In the dashboard, **Revoke** and **Delete** on an owner key open **Transfer Ownership First** instead of a confirmation, and administrators get a shortcut to the Synced Organizations panel.

Uninstalling an add-on whose credential owns organizations is refused the same way. Suspend the add-on to stop it at once, and transfer or delete its organizations before uninstalling.

Each refusal is recorded on the refused action (for example `integration.api_key.revoke`) with result `denied` and error code `key_owns_organizations`. The record names the same organizations the response did, and counts the rest.

!!! note "The add-on reconcile sweep"
    The sweep that revokes add-on credentials no installation claims, or whose organization reaches no cluster any more, still revokes them: such a credential must stop. Its audit record then names the organizations the revocation leaves frozen, so an administrator can transfer them.

---

## Recover a frozen organization

An organization is frozen when its owner key is expired, revoked, inactive or deleted. Symptoms:

- its **Owner State** is Expired, Revoked, Inactive or Missing, and administrators see the "no longer works" callout;
- member and policy changes made in OrcaHub stop reaching Orcastra; and
- provisioning for it answers `409 Organization '<slug>' is owned by a different integration key`.

To recover:

1. Make sure the OrcaHub connection uses an active OrcaHub key that holds `inventory.read` and `org.sync`. If it does not, create one in **Settings > Integrations** and configure the connection with it.
2. In **Synced Organizations**, choose **Transfer Owner** on each frozen organization and pick that key.
3. Wait for the connection's next sync, or trigger one from OrcaHub. The organization's members and policies resume updating.

An organization whose owner state is **Unknown** is not necessarily frozen: its owner key could not be read. Check Vault health (see [Troubleshooting](troubleshooting.md#vault-issues)) and reload before transferring.
