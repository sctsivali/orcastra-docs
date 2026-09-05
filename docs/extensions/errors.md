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

**422 can mean a missing credential header.** Both headers are declared required, so omitting one is a validation failure rather than an authentication one. A wrong or revoked credential answers 401; an absent one answers 422. A client that only handles 401 will misread the second as a malformed request.

**503 is not your fault.** On this API it usually means the secret store is unreachable, so credential operations cannot be served. The request was fine. Retry it.

## Refusals that name a category

A refused URL says why in general terms:

```json
{"detail": "endpoint_url is not a URL this server may call: host resolves to an internal address"}
```

It never names the address it resolved to. That is deliberate: a refusal that reported the address would let anybody map the deployment's network one guess at a time.

## Distinguishing a dead cluster from an empty one

A cluster that is unreachable does not necessarily produce an error status. Reads may answer `200` with nothing in them. Check the response body for the emptiness rather than assuming a success status means the cluster answered.
