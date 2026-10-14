# Error Responses

**Every failure returns a JSON object with a `detail` field. The status says what to do about it.**

---

```json
{"detail": "This installation does not reach that cluster"}
```

Validation failures return the same status with a list, naming the field:

```json
{"detail": [{"loc": ["body", "endpoint_url"], "msg": "must use https", "type": "value_error"}]}
```

## What each status means

| Status | Cause | What to do |
|---|---|---|
| 400 | The request was malformed, or a URL was refused | Fix the request. Do not retry unchanged |
| 401 | Missing, wrong, expired or revoked credential | Stop. Check `GET /extensions/self`, and if it also answers 401 the credential is gone |
| 403 | Authenticated, but this credential is not permitted | Stop. A capability or a cluster is missing from the grant |
| 404 | Not found, or not visible to this credential | Treat as not found. The two are deliberately indistinguishable |
| 409 | The request conflicts with current state | Read the state and decide. Retrying unchanged will conflict again |
| 422 | A field failed validation, or a required header is missing | Fix the field named in `detail`. Sending only one of the two credential headers lands here rather than on 401 |
| 429 | Rate limited | Back off with jitter, then retry |
| 503 | A dependency is unavailable, usually the secret store | Retry with backoff. Nothing is wrong with your request |

## The two worth reading twice

**404 and 403 are not interchangeable.** A resource outside your reach answers 404, not 403, so a caller cannot use the difference to discover that something exists. Do not treat a 404 as proof that a cluster or an installation is gone.

**422 can mean a missing credential header.** Both headers are declared required, so omitting one is a validation failure rather than an authentication one. A wrong or revoked credential answers 401; an absent one answers 422. A client that only handles 401 will misread the second as a malformed request. The MCP endpoint is the exception: it answers 401 for a missing header too (see below).

**503 is not your fault.** On this API it usually means the secret store is unreachable, so credential operations cannot be served. The request was fine. Retry it.

## Errors over MCP

`/api/v1/mcp` uses the same credential, but a failure takes one of two channels.

A credential or transport problem is an HTTP status, before any tool runs:

| Status | Cause |
|---|---|
| 401 | Missing, wrong, revoked or expired credential, with `WWW-Authenticate: Bearer realm="orcastra-mcp"`. A missing header is 401 here, not 422 |
| 405 | `GET` or `DELETE`. The server is stateless and answers `POST` only |
| 413 | The request body is over the transport's cap |
| 429 | The per-credential budget is spent. Honour `Retry-After` |

Anything a tool could not do comes back as a tool result with `isError` set, and its text starts with a code in brackets, for example `[cluster_unavailable] Cluster c1 recently unreachable`:

| Code | What to do |
|---|---|
| `forbidden` | Stop. The credential lacks a capability, a cluster or a project. `orcastra_whoami` lists what it holds |
| `invalid_request` | Fix the arguments |
| `not_found` | Treat as not found. An operation poll answers this both for an operation LXD has already forgotten and for one in a project this credential does not hold, deliberately the same answer |
| `conflict` | Narrow the request. An instance name held by two projects needs `cluster_id` and `project` |
| `overloaded` | Wait the time the text gives, then retry |
| `cluster_unavailable` | Retry later. Nothing is wrong with the request |
| `timeout` | The work may still be running. Poll it with `lxd_get_operation` instead of resending, passing the operation's `project` when the credential is limited to named projects |
| `operation_failed` | The cluster answered, but not usefully. Report it |

An unknown tool or a method the server does not serve is a JSON-RPC error (`-32601`), not a tool result.

## Refusals that name a category

A refused URL says why in general terms:

```json
{"detail": "endpoint_url is not a URL this server may call: host resolves to an internal address"}
```

It never names the address it resolved to. That is deliberate: a refusal that reported the address would let anybody map the deployment's network one guess at a time.

## Distinguishing a dead cluster from an empty one

A cluster that is unreachable does not necessarily produce an error status. Reads may answer `200` with nothing in them. Check the response body for the emptiness rather than assuming a success status means the cluster answered.
