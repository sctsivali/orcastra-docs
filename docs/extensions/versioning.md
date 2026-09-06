# Versioning And Deprecation

**Everything is under `/api/v1`. Additive changes ship without notice; anything that could break a working add-on does not.**

---

## What will not break you

- A new field appearing in a response
- A new optional field accepted in a request
- A new capability appearing in the vocabulary
- A new event type appearing in the catalogue
- A new endpoint
- A new value in a field documented as open-ended

Ignore fields you do not recognise, and do not assert on the exact set of keys in a response.

## What counts as breaking

- Removing or renaming a response field
- Making an optional request field required
- Narrowing what an existing capability permits
- Changing the meaning of an existing event without changing its name
- Removing an endpoint

Any of these arrives as a new version, announced in the release notes for the deployment you integrate with.

## Publishing a new version of your own add-on

`POST /api/v1/extensions/{slug}/versions` with the whole manifest, under a version numerically
greater than the published one. Not a patch: an operator approves a document, so a diff would
leave them approving something they cannot read on its own.

What happens to the installations that already exist:

- They keep the reach they were granted and go on working. Nothing about them moves.
- Their page shows the version they consented to, with the new one named beside it as available.
- Because the manifest hash has moved, changing one of them asks its operator to approve the new
  manifest first. A new version may ask for more, and nobody should acquire that quietly.

`PATCH /api/v1/extensions/{slug}` is for presentation: name, description, the two links, and
status. It cannot change the slug, the version, or anything you are asking for, and it leaves the
consent hash alone, so fixing a typo does not invalidate every existing agreement.

## Events carry their own version

An event name ends in `.v1`. A `v2` of the same event is a new name in the catalogue and does not replace the one you subscribed to, so a payload change never arrives unannounced under a name you already handle.

## The vocabulary is served, not published

Read capabilities, events and settings field types from `GET /extensions/vocabulary` rather than copying them into your source. A client holding its own copy will eventually offer something the deployment does not enforce, and the operator sees a form that lies about what it is granting.

## Operation names in the API document

The [curated API document](api-reference.md) gives every endpoint a stable `operationId` derived from its method and path. Generated clients name their methods from those, so they do not change when internal code is renamed.

## Credential lifetime

An installation credential expires after a year by default and warns from fourteen days out. Rotating issues a new credential; asking for a grace window leaves the old one working for a while so you can deploy without downtime. There is no way to read a secret back after it is issued, so plan for rotation rather than for recovery.
