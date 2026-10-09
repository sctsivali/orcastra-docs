# Integration Keys and Organization Ownership

**Every OrcaHub organization synced into Orcastra CMP is owned by an integration key, or, where an administrator has turned on several owner keys, by a set of them. Only an owner key can keep it in sync, so rotate a key's secret instead of replacing the key, and make sure another key owns an organization before you end the one that owns it.**

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
- The **Synced Organizations** panel lists every synced organization with its owner key and the owner's state. A partner sees the owner key's name and state only when they are a partner of that key's own organization (see [Rotate a secret](#rotate-a-secret-without-changing-the-key-id) for how Orcastra recognizes it, which needs a sale through the key). For any other organization they belong to, for example as a tenant, or as the founder of a buyer organization that carries the seller's key, and for every organization of a key before its first sale, the owner state reads **Not Shown** to everyone but administrators.

| Owner state | Meaning |
|---|---|
| Active | The owner key works. |
| Expiring | The owner key works and expires within 14 days. |
| Expired, Revoked, Inactive | The owner key no longer works. The organization is frozen until it is transferred. |
| Missing | The owner key no longer exists. The organization is frozen until it is transferred. |
| Unknown | The owner key's record could not be read (for example, Vault is unreachable). Retry before acting. |
| Unclaimed | No key owns the organization yet. |

Administrators also see a callout counting the organizations whose owner no longer works.

The same information is in the API: `GET /api/v1/integrations/organizations` returns `owner_key_name`, `owner_key_state`, `owner_key_expires_at`, `owner_key_expiry_state` and `cluster_ids` for each organization, and `GET /api/v1/integrations/api-keys` returns `owned_organization_count`, `owned_organization_slugs` and `owned_organization_hidden_count` for each key. The four `owner_key_*` fields are `null` unless the caller is an administrator or a partner of the owner key's own organization.

---

## Rotate a secret without changing the key ID

Rotate the secret whenever a key's secret may have leaked, or on your regular schedule. The key ID stays, so every organization the key owns keeps syncing once OrcaHub has the new secret.

1. In **Settings > Integrations**, choose **Rotate Secret** on the key's card, type the key's name (or its ID, for a key without a name) to confirm, and choose **Rotate Secret**. The control appears on active keys that are not an add-on's credential.
2. Copy the new secret from the dialog. It is shown once.
3. Update the OrcaHub connection with the new secret.

The previous secret stops working at once on every backend worker while Redis is reachable: the rotation publishes the revocation there, and each worker checks it before trusting its cached copy of the key. Without Redis, a worker that cached the old secret keeps accepting it for up to about 30 seconds (`ORCASTRA_API_KEY_CACHE_TTL`), and a request already in flight completes either way. Until OrcaHub has the new secret, its syncs are refused with `401`.

Callbacks follow the secret. Every request Orcastra makes to OrcaHub carries the key's credential: the member updates and the lifecycle webhook carry its secret, and **Refresh organizations** carries its hash. So Orcastra keeps the callback URL a sync stores together with a tag of the secret that sync used, and uses the URL only while that secret is still the key's secret. A rotation also clears the stored URL. A sync still running with the old secret cannot store a URL after the rotation, and a URL stored with the old secret never receives the new one. The OrcaHub connection stores its URL again on its first sync with the new secret. Until then, Orcastra sends no callbacks for the key, and **Refresh organizations** answers `400` "No callback URL is set for this API key's current secret".

!!! note "After upgrading"
    A callback URL stored by a release without this tag is not used. Each key's callbacks and **Refresh organizations** resume after its next sync, about 5 minutes on OrcaHub's default schedule.

Who may rotate:

- An administrator may rotate any key.
- A partner who manages the key may rotate it only when every cluster the key grants is within their access. This applies to the key's creator too.
- When the key owns OrcaHub organizations, the partner must also be a current partner of the key's own organization, the seller's organization its OrcaHub connection syncs. A partner of a buyer organization the key provisioned does not count, and neither does having created the key or an organization. This applies to the key's creator too, so a creator who has left the seller's organization needs an administrator, also when they later buy from it.
- Orcastra recognizes the key's own organization only through a sale: it is the oldest organization the key synced, and it must hold a cluster one of its own partners registered, on which the key has provisioned a buyer. Until that can be shown, only an administrator can rotate the key. That includes a new key before its first sale, and a key whose seller organization was deleted and synced again.
- A partner who did not create the key also needs, on a cluster another organization also holds, every project the key names to be one they can see.

Anyone else gets `403` (or `404` for a key they cannot manage), and a refused rotation is recorded in the audit log.

API equivalent:

```bash
curl -X POST https://<dashboard>/api/v1/integrations/api-keys/<key_id>/rotate-secret \
  -H "Authorization: Bearer <token>"
```

The response carries `api_key_id` (unchanged) and `api_key_secret` (new). A revoked, inactive or expired key answers `409`; create a new key instead. An add-on credential answers `409`; rotate it from its installation.

A `503` answer says which of these happened:

- The callback URL could not be cleared, or the organizations the key owns could not be checked: nothing changed, and the old secret still works. Try again.
- The rotation did not confirm: a new secret may already be stored, and it was not shown. Rotate again before using the key.

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

### Withdrawing tenant.provision asks first

Withdrawing `tenant.provision` from a key that owns an active OrcaHub organization is not refused, because an operator may want it, but it is not silent either. Without it, the key still syncs those organizations and their members, but provisioning buyer dashboard access for them stops: an order completed in OrcaHub no longer gives its buyer access in Orcastra, and no other key may provision for an organization this key owns.

`PUT /api/v1/integrations/api-keys/<key_id>/capabilities` answers `409` with `error: provisioning_would_stop`. The response names the consequence and the organizations you may see, and counts the rest. Nothing changes. The same request with `"force": true` withdraws it. Its record on `integration.api_key.capabilities.update` carries `forced: true` and the IDs of the owned organizations you may see, with the full and hidden counts.

In the dashboard, **Edit Capabilities** on the key's card shows the consequence when you save, and **Withdraw Anyway** confirms. The usual rules for who may edit the key apply. `inventory.read` and `org.sync` stay refused as above, with or without `force`.

!!! note "The add-on reconcile sweep"
    The sweep that revokes add-on credentials no installation claims, or whose organization reaches no cluster any more, still revokes them: such a credential must stop. Its audit record then names the organizations the revocation leaves frozen, so an administrator can transfer them.

---

## Several owner keys per organization

With `INTEGRATION_MULTI_OWNER_ENABLED=true` (off by default; needs a restart), an organization can be owned by up to five keys at once. Every key in its owner set may sync it, update its members and policies and provision tenant access for it. One of them is the **primary**: the key Orcastra calls OrcaHub back with. A provider with two OrcaHub connections to the same control plane can then use either one, and ending one key no longer freezes the organization.

A key joins an owner set only by being vouched for: by an owner key that also holds the new key's secret (two-key enrollment, below), or by an administrator. Any key outside the set is refused exactly as before. While the setting is off, the routes below answer `404` before they authenticate the caller or read the body, `validate` reports no features, and every organization keeps the single owner it had.

Co-ownership does not change who manages a key. Who may read, revoke, delete, edit or rotate a key still follows the organizations the key claimed, was provisioned for or was transferred to, as with a single owner. A key that was enrolled or added by an administrator owns the organization but does not put it on its minter's side, so the partner who provisioned an organization cannot manage a key that the organization's founder enrolled there.

### What changes when a key ends

Revoking, deleting or uninstalling a key, or withdrawing its `inventory.read` or `org.sync`, is refused only for organizations where it is the **only owner able to sync**: an active OrcaHub key holding both capabilities. Where another such owner exists, the end goes through: the key leaves those owner sets (revoke, delete, uninstall) or stays in them without being primary (a withdrawn capability), and the oldest able owner becomes the primary in the same step. If another owner's record cannot be read, or the owner sets cannot be saved, nothing changes and the answer is `503`. Withdrawing `tenant.provision` asks first only for organizations no other owner key provisions for.

The owner sets are checked before the key is changed in Vault. If the database then fails to save them after the key was changed, the answer is `503` with "The key was changed, but the organizations it owns could not be updated." The key is already revoked, deleted or narrowed and stops working at once. Finish the change in **Synced Organizations > Owner Keys** on each organization the key owned: remove the key, or make another key the primary.

Each change to an owner set is recorded as `integration.organization.owner.add`, `.remove` or `.primary`, with the set before and after. An administrator's **Transfer Owner** still replaces the whole set with the new key; its answer lists the co-owners it removed (`removed_owner_key_ids`).

### Manage owner keys (administrators)

In **Synced Organizations**, the row action **Owner Keys** lists the set with its primary and each key's state, and offers **Add Owner Key**, **Make Primary** and **Remove**, each with a reason for the audit record. The last owner cannot be removed; use **Transfer Owner** instead. The same in the API, all administrator only:

| Method | Path | Body | Refusals |
|---|---|---|---|
| POST | `/api/v1/integrations/organizations/<org_id>/owner-keys` | `{"api_key_id", "reason"}` | `409` the transfer codes above, `409 owner_set_full`, `409 organization_unclaimed` |
| DELETE | `/api/v1/integrations/organizations/<org_id>/owner-keys/<key_id>?reason=...` | | `409 last_owner`, `409 no_able_owner`, `503` |
| PUT | `/api/v1/integrations/organizations/<org_id>/primary-key` | `{"api_key_id", "reason"}` | `409 key_not_an_owner`, the transfer codes above |

Each answers `{"organization_id", "organization_slug", "external_id", "primary_key_id", "owner_key_ids", "changed"}`. `GET /api/v1/integrations/organizations` lists each organization's `owner_keys` (key ID, name, state, expiry, whether it is the primary, how it joined) under the same visibility rule as the `owner_key_*` fields, and each key in `GET /api/v1/integrations/api-keys` carries `sole_owned_organization_count`.

### Two-key enrollment (integrations)

An integration that holds an owner key and a second key, as OrcaHub holds every connection's key, can make the second one a co-owner itself. All three calls take the usual key headers; `validate` and `whoami` report the feature `organization_owner_keys.v1` while it is available.

`GET /api/v1/integrations/external/organizations/<external_id>/owner-keys` (`inventory.read`) tells the calling key where it stands, and writes nothing:

```json
{"status": "owner", "primary_key_id": "oak_...", "owner_key_ids": ["oak_...", "oak_..."]}
```

`status` is `owner`, `not_owner` or `unclaimed` (also for an organization Orcastra has not seen). Only an owner is told the primary and the set.

`POST /api/v1/integrations/external/organizations/owner-keys` (`org.sync`), authenticated as an owner key of the organization:

```json
{"organization_external_id": "<uuid>", "new_key_id": "oak_...", "new_key_secret": "oas_..."}
```

answers `{"organization_external_id", "primary_key_id", "owner_key_ids", "added"}`. A key already in the set answers `"added": false`. The new key's secret is checked and discarded; it is never stored, logged, recorded or repeated in an error, including a `422`. The primary does not change.

Orcastra first checks that the calling key owns the organization. Only then does it read anything about the new key. A key that is not an owner, an organization Orcastra does not know and an organization nobody owns yet all get the same `403 not_an_owner`.

| Status | `error` | Cause |
|---|---|---|
| 403 | `not_an_owner` | The calling key does not own the organization, the organization does not exist or has no owner, or the calling key can no longer own it (ended, missing a sync capability, an add-on credential). |
| 403 | `new_key_unverified` | No such key, or the secret is wrong (one sentence for both). After 5 failures for the same calling key and new key, that pair is locked out of enrollment for an hour. |
| 403 | `new_key_outside_organization` | The new key was minted by someone who did not mint the calling key, did not create the organization and has not been a partner of it for at least 24 hours. An administrator can still add it. |
| 409 | `owner_set_full` | The organization already has five owner keys. |
| 409 | `target_key_*` | The new key could not own the organization (the transfer codes above). |
| 422 | `same_key` | The new key is the calling key. |
| 422 | | The body is not valid. Each error names the field and the problem, never the value. |
| 429 | `rate_limited` | More than 5 enrollments a minute or 30 a day for the calling key, or for the organization. Only an owner's attempts count toward the organization's limit. |
| 503 | | Vault could not be read. Nothing changed and nothing counts as a failure; try again. |

`DELETE /api/v1/integrations/external/organizations/<external_id>/owner-keys/self` (`org.sync`) takes the calling key out of the set and answers `{"removed", "primary_key_id", "owner_key_ids"}` (`"removed": false`, with no set, for a key that is not an owner). If the key was the primary, the oldest owner able to sync becomes the primary. It answers `409 last_owner` while no other owner can sync the organization.

### If an owner key leaks

A co-owner survives the rotation of another key, so after a leak check the whole set, not only the leaked key:

1. Rotate the leaked key's secret (or revoke it, if another able owner carries its organizations).
2. In **Synced Organizations > Owner Keys**, review every organization the key owned. Remove any owner key you do not recognize, and rotate any you are unsure of.
3. Search the audit log for `integration.organization.owner.add` with `trigger: enroll` and the leaked key as `proof_key_id`: those are the keys it vouched for.
4. Review the organization's partners. An owner key can write members, and a key minted by a partner can be enrolled once that partner row is 24 hours old. Remove any partner you do not recognize.

!!! warning "Known limits"
    - Orcastra does not record which key wrote a partner row, so enrollment relies on the row's age instead. Changing a member's role keeps the date they joined. A leaked owner key can therefore promote a member who joined more than 24 hours ago to partner and, if that member can mint integration keys, enroll one of their keys right away. Step 4 above covers this.
    - Orcastra does not tell an organization's partners when a key joins its owner set. The audit record of each enrollment counts the partners (`partner_count`) who would be told.

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
