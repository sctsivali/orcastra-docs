# Manifest Reference

**Every field an add-on may declare, what validates it, and what an operator sees.**

---

The manifest is the whole of what you ask for. It is submitted as JSON, never fetched from a URL
you host, and Orcastra records a hash of it against every consent. That makes it a contract rather
than configuration: an operator reads it, approves it, and what they approved cannot change
underneath them.

The whole document is capped at 16 KB.

## A complete example

```json
{
  "slug": "acme-backup",
  "name": "Acme Backup",
  "publisher": "Acme Ltd",
  "version": "1.0.0",
  "description": "Snapshots instances on a schedule and keeps them for a configurable window.",
  "homepage_url": "https://acme.example.com/backup",
  "support_url": "https://acme.example.com/support",
  "capabilities": ["inventory.read", "lxd.proxy"],
  "scope": {
    "permission": "write",
    "clusters": "selected",
    "projects": "selected"
  },
  "settings": [
    {
      "key": "retention_days",
      "type": "integer",
      "label": "Retention",
      "help": "How long a snapshot is kept.",
      "min": 1,
      "max": 90,
      "default": 7
    },
    {
      "key": "api_token",
      "type": "secret",
      "label": "Acme API token",
      "required": true
    }
  ],
  "webhook_url": "https://acme.example.com/orcastra/hook",
  "webhook_events": ["orcastra.instance.created.v1", "orcastra.instance.deleted.v1"]
}
```

## Identity

| Field | Required | Rule |
| --- | --- | --- |
| `slug` | yes | 3 to 64 characters, lowercase ASCII letters, digits and hyphens, starting and ending with a letter or digit. Permanent. A refused slug is refused, never quietly normalised, because a character that folds onto another is how two add-ons end up sharing a name. |
| `name` | yes | 1 to 100 characters. What an operator sees. |
| `publisher` | yes | 1 to 100 characters. Who they are trusting. |
| `version` | yes | Semantic version, at most 32 characters. A later version must be numerically greater: `10.0.0` is above `9.0.0`. |
| `description` | no | Up to 2000 characters. One line an operator reads while deciding. |
| `homepage_url` | no | Up to 512 characters, `https` only. Rendered, never fetched, so it is checked for shape rather than resolved. A link behind a CDN is fine. |
| `support_url` | no | As `homepage_url`. |

## Capabilities

`capabilities` is a list of one to ten names, each from the served vocabulary. Read the current
list from `GET /extensions/vocabulary` rather than copying it, or your form will eventually offer
something the server refuses. Duplicates are refused rather than deduplicated.

Five of them reach inside a guest and are marked on the consent screen with the server's own
reason: `instance.exec`, `instance.files`, `desktop.view`, `desktop.input` and
`desktop.clipboard`. Asking for one of these means the organization must already have consented to
guest access, separately, before your installation can be created. See
[capabilities](capabilities.md).

## Scope

`scope` describes the **shape** of what you want, never a customer's cluster by name. You cannot
know their cluster ids and should not ask.

| Field | Values | Meaning |
| --- | --- | --- |
| `permission` | `read`, `write`, `admin` | The most an installation may be granted per cluster. |
| `clusters` | `selected`, `all` | Whether you expect one or two clusters, or every cluster an organization holds. |
| `projects` | `selected`, `all` | The same question for LXD projects. `all` is what a cluster-wide operation needs. |

What you actually reach is decided at install time: your ask, intersected with what the installing
operator holds, intersected with the clusters the organization is still bound to. It is recomputed
on every call, so it narrows on its own when their access narrows. See [scope](scope.md).

## Settings

`settings` declares the form Orcastra draws for your add-on. Up to 32 fields, keys unique.

Your add-on ships no interface for this: it declares fields and the dashboard renders them, so an
operator configures you without leaving the console. Read the saved values back through
`GET /extensions/self`.

| Key | Required | Notes |
| --- | --- | --- |
| `key` | yes | How you read the value back. |
| `type` | yes | One of `string`, `secret`, `integer`, `boolean`, `enum`, `url`. |
| `label` | yes | Shown above the input. |
| `help` | no | Shown under it. |
| `required` | no | Blocks saving while empty. |
| `default` | no | Prefilled. Never on a `secret`. |
| `min`, `max` | no | `integer` only. |
| `options` | for `enum` | The permitted values. |

A `secret` is write-only. It is stored beside your credential, never returned to the dashboard,
and a read there answers with the names of the fields that hold a value rather than the values.
Your own `GET /extensions/self` is the one place the values come back, because you are the party
that was given them. Leaving a secret blank on a later save keeps what is stored rather than
clearing it, so saving an unrelated field does not destroy a token nobody retyped.

## Webhooks

| Field | Required | Rule |
| --- | --- | --- |
| `webhook_url` | no | Up to 512 characters, `https` required. The one URL Orcastra stores and later contacts, so it is fully resolved and checked: a private, loopback, link-local or otherwise internal address is refused, and so is a name that resolves to one. |
| `webhook_events` | no | Event names from the served vocabulary. Refused without a `webhook_url`, since there would be nowhere to deliver them. |

These are defaults. An operator can subscribe, change and remove endpoints on the installation's
own page afterwards, and a subscription only becomes active once the endpoint answers a signed
verification ping. See [webhooks](webhooks.md).

## Publishing a new version

`POST /api/v1/extensions/{slug}/versions` takes a whole manifest, not a patch, under a version
greater than the published one.

Existing installations are untouched. They keep the reach they were granted and stay on the
version they consented to, which is what their installation page shows, with the newer version
named beside it as available. Because the manifest hash has moved, changing one of those
installations asks its operator to approve the new manifest first. That is deliberate: a new
version may ask for more, and nobody should acquire that silently.

`PATCH /api/v1/extensions/{slug}` is for presentation only: name, description, the two links and
status. It cannot change the slug, the version, or anything you are asking for, and it does not
move the consent hash, so correcting a typo does not invalidate anybody's agreement.
