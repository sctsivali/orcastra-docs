# Settings

**Declare the fields your add-on needs. The dashboard renders the form, stores the values, and lets you read them back.**

---

Your add-on ships no user interface, so it does not draw its own settings page. It declares fields in its manifest and Orcastra draws them.

## The field types

| Type | Rendered as | Extra keys |
|---|---|---|
| `string` | Single-line text | |
| `secret` | Password input, write-only | |
| `integer` | Number input | `min`, `max` |
| `boolean` | Switch | |
| `enum` | Select | `options` |
| `url` | Text input, `https` enforced | |

Every field takes `key`, `type`, `label`, and optionally `help`, `required` and `default`.

```json
"settings": [
  {"key": "retention_days", "type": "integer", "label": "Retention",
   "help": "How long snapshots are kept", "min": 1, "max": 90, "default": 7},
  {"key": "mode", "type": "enum", "label": "Mode", "options": ["fast", "thorough"], "default": "fast"},
  {"key": "api_token", "type": "secret", "label": "Acme API token", "required": true}
]
```

The vocabulary is closed deliberately. An open schema language would let a manifest describe a form the dashboard cannot render consistently, and the first place that shows up is in front of an operator who is deciding whether to trust you.

## Secrets

A secret is write-only. It is stored beside the installation's credential, it is never returned to a browser, and reading an installation tells you which secret fields hold a value rather than what those values are.

Two rules follow from that.

A secret field cannot declare a `default`. A default would be a credential written into a document every prospective operator can read.

Submitting a settings update without a secret key leaves the stored value alone. Only sending a new value replaces it. Without that, saving an unrelated field would blank every credential nobody happened to retype.

## Reading them back

Your add-on reads its own resolved settings, secrets included, through its own credential:

```bash
curl -sS "$ORCASTRA_API_URL/api/v1/extensions/self" \
  -H "X-API-Key-ID: $ORCASTRA_API_KEY_ID" \
  -H "X-API-Key-Secret: $ORCASTRA_API_KEY_SECRET"
```

The `settings` object in that response is the merged result: the values an operator typed, plus any defaults they left alone.
