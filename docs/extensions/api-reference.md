# API Reference

**Every endpoint an add-on may call, and what each one costs.**

---

The machine-readable document is served by the deployment itself:

```bash
curl -sS "$ORCASTRA_API_URL/api/v1/openapi.public.json"
```

Browse it at `/api/v1/docs/public`. Feed it to your own generator to produce a client. It is curated: it describes only the surface an add-on may call, not the dashboard's own API.

A copy generated from a current build is committed here as [`orcastra-extensions-v1.yaml`](orcastra-extensions-v1.yaml) for readers without a deployment to hand. The live document is authoritative.

!!! note "What this document deliberately leaves out"
    It describes the endpoints **your add-on's credential** may call, and nothing else. Publishing
    an add-on, installing one, and managing its webhook subscriptions are done by a person signed
    into the dashboard, authenticated by their session rather than by your credential, so those
    endpoints are not here and a generated client will not contain them.

    That is the boundary, not an omission: your add-on is never given the ability to publish or
    install itself.

    Those endpoints are documented in prose instead. Publishing and versioning are on
    [the manifest reference](manifest.md), installing is on [installing an add-on](installing.md),
    and subscriptions are on [webhooks](webhooks.md).

## Authenticating

Both headers, together. Neither alone authenticates anything.

```bash
-H "X-API-Key-ID: oak_..." -H "X-API-Key-Secret: oas_..."
```

## Endpoints

### About this installation

| Method | Path | Capability |
|---|---|---|
| GET | `/extensions/self` | none |

What this credential is installed as, what it reaches, and its resolved settings. Poll this to notice a change rather than caching what you were told at install. It requires no capability on purpose: the one call that diagnoses a credential has to work for the credentials that need diagnosing.

### Credential checks

| Method | Path | Capability |
|---|---|---|
| GET | `/integrations/whoami` | none |
| POST | `/integrations/validate` | none, credentials go in the body |

### Inventory

| Method | Path | Capability |
|---|---|---|
| GET | `/integrations/clusters` | `inventory.read` |
| GET | `/integrations/projects` | `inventory.read` |
| GET | `/integrations/instances` | `inventory.read` |
| GET | `/integrations/instances/{name}` | `inventory.read` |
| GET | `/integrations/operations/{operation_id}` | `inventory.read` |
| GET | `/integrations/regions` | `inventory.read` |

```bash
curl -sS "$ORCASTRA_API_URL/api/v1/integrations/instances" \
  -H "X-API-Key-ID: $ORCASTRA_API_KEY_ID" \
  -H "X-API-Key-Secret: $ORCASTRA_API_KEY_SECRET"
```

### Cluster operations

| Method | Path | Capability |
|---|---|---|
| POST | `/integrations/proxy` | `lxd.proxy` |
| POST | `/integrations/raw-proxy` | `lxd.proxy` |

Both reach a cluster's own API through Orcastra, which applies the credential's cluster and project grant to every call. Paths that reach inside a guest cost a further capability of their own, and the certificate store is never reachable at any level.

### Uploads

| Method | Path | Capability |
|---|---|---|
| POST | `/integrations/image-uploads` | `storage.upload` |
| HEAD, PATCH | `/integrations/image-uploads/{session_id}` | `storage.upload` |
| POST | `/integrations/image-uploads/{session_id}/finalize` | `storage.upload` |
| POST | `/integrations/iso-uploads` | `storage.upload` |
| HEAD, PATCH | `/integrations/iso-uploads/{session_id}` | `storage.upload` |
| POST | `/integrations/iso-uploads/{session_id}/finalize` | `storage.upload` |

Resumable uploads. `HEAD` reports how much arrived, so an interrupted transfer continues rather than restarting.

### Organizations and tenants

| Method | Path | Capability |
|---|---|---|
| POST | `/integrations/sync` | `inventory.read`, plus `org.sync` to write |
| POST | `/integrations/external/sync-policies` | `tenant.provision` |
| GET | `/integrations/external/policies` | `tenant.provision` |
| POST | `/integrations/provision-tenant-access` | `tenant.provision` |
| POST | `/integrations/revoke-tenant-access` | `tenant.provision` |

These write access control. They are marked on the consent screen for that reason, and most add-ons should not ask for them.

## MCP

The same credential authenticates an MCP client at `/api/v1/mcp`, over JSON-RPC. It is not modelled as OpenAPI paths, because JSON-RPC tools rendered as REST paths would describe something no client can call. The capability and cluster grant apply identically: a tool a credential may not use is not offered to it.

```json
{
  "mcpServers": {
    "orcastra": {
      "type": "http",
      "url": "https://<YOUR_ORCASTRA_API>/api/v1/mcp",
      "headers": {
        "X-API-Key-ID": "oak_...",
        "X-API-Key-Secret": "oas_..."
      }
    }
  }
}
```

A remote HTTP entry, not a spawned binary. Orcastra serves MCP itself.
