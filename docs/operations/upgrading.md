# Upgrading a Deployment

Moving an existing VM 4 deployment from one pinned release to the next. For a first
install, follow [VM 4 - Orcastra CMP](../deployment/vm4-dashboard.md) instead.

Everything on this page is the procedure, which does not change between releases. Anything
specific to a version lives in the [release notes](#release-notes) table at the bottom, one
row per release. Check that table, then follow the steps.

---

## Before you start

Set these once and reuse them:

```bash
cd /opt/orcastra-dashboard          # wherever docker-compose.prod.yml and .env live
VERSION=1.0.0-RC4                   # the release you are moving to
RELEASE=v${VERSION}                 # the matching git tag
```

## Step 1: Preflight

The preflight is read-only. It changes nothing and it is the only thing that will tell you,
before you pull, whether this particular deployment is going to come back up.

Fetch the compose file that belongs to the release, so the preflight can compare it against
the one you are running:

```bash
curl -fsSL -o docker-compose.prod.yml.new \
  "https://raw.githubusercontent.com/sctsivali/orcastra-cmp/${RELEASE}/docker-compose.prod.yml"
```

Then run whatever preflight tooling your team keeps on the deployment VM against it. Operator
tooling is not distributed from this repository: it holds credentials paths and deployment
assumptions that belong to the team running the estate, not to the product. If you have none,
the rest of this page is the procedure it would automate, and every check below can be run by
hand.

At minimum, before you pull, establish these four. Each one takes a site down or corrupts
state, and none of them announces itself:

- `AUTHENTIK_ISSUER` is set and **ends with a trailing slash**. See the danger note below.
- The database's alembic stamp exists in the image you are moving to. If it does not, the
  entrypoint cannot resolve it, runs under `set -e`, and the container dies before the API
  starts. That is a crash loop, not a downgrade.
- Every variable the new compose file reads with no default is present and non-empty in
  `.env`. Present-but-empty is not the same as set: `OPENSEARCH_HOST=` still makes compose
  refuse to start the whole stack.
- Both halves of the release name the same commit:

```bash
for s in backend frontend; do
  docker image inspect "svlct/orcastra-dashboard:${s}-${VERSION}" \
    --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
done
```

Those two must print the same commit. One earlier release did not, and shipped a backend and
a frontend built eight days apart under a single tag.

!!! danger "The finding that takes a site down"
    If `AUTH_ENABLED` is true and `AUTHENTIK_ISSUER` is empty, stop and fix that first.

    Builds before v1.0.0-RC4 returned `None` for the current user in that configuration, and
    every RBAC dependency read `None` as development mode and fabricated a full Admin. So a
    deployment in this state has been serving unauthenticated callers as administrators.
    From v1.0.0-RC4 the backend answers 503 to every request instead, which is correct and
    also means the dashboard goes completely dark the moment you upgrade.

    Both halves matter. Set `AUTHENTIK_ISSUER` before you pull.

## Step 2: Back up

A schema migration may run on the first start of the new backend, so this is not optional.

```bash
docker exec orcastra-dashboard-postgres \
  pg_dump -U orcastra -Fc orcastra_dashboard > "pre-${VERSION}.dump"
cp .env .env.bak-$(date -u +%Y%m%dT%H%M%SZ)
cp docker-compose.prod.yml docker-compose.prod.yml.bak-$(date -u +%Y%m%dT%H%M%SZ)
```

## Step 3: Replace the compose file

```bash
mv docker-compose.prod.yml.new docker-compose.prod.yml
```

The preflight printed the diff. If it is larger than the release notes suggest, your copy
predates the release you are coming from, and the extra lines are listed in
[what the compose file gives you](../deployment/vm4-dashboard.md#what-this-compose-file-gives-you-that-older-copies-did-not).
Several of those lose data rather than features, so read them before skipping this step.

One in particular is easy to miss: the frontend service has no `env_file`, so every variable
it reads arrives through its `environment:` block. Adding a variable to `.env` without
replacing the compose file does nothing at all.

## Step 4: Pin the tag and deploy

```bash
sed -i "s/^APP_VERSION=.*/APP_VERSION=${VERSION}/" .env
sed -i "s/^API_VERSION=.*/API_VERSION=${VERSION}/" .env

docker login
docker compose -f docker-compose.prod.yml pull

# with the images now local, check the schema the hard way: the revision the database is
# stamped with must exist in the image you are about to start, or the entrypoint cannot
# resolve it and the container dies before the API starts
STAMP=$(docker exec orcastra-dashboard-postgres \
  psql -U orcastra -d orcastra_dashboard -tAc 'SELECT version_num FROM alembic_version')
docker run --rm --entrypoint alembic \
  "svlct/orcastra-dashboard:backend-${VERSION}" history | grep -q "$STAMP" \
  && echo "ok: the target image ships $STAMP" \
  || echo "STOP: the image does not ship $STAMP; starting it is a crash loop"

docker compose -f docker-compose.prod.yml up -d
docker logout
```

Watch the entrypoint apply the migrations:

```bash
docker compose -f docker-compose.prod.yml logs backend | grep -E 'entrypoint|Running upgrade'
```

Expected, in order: `reconciling database with Alembic (if needed)`,
`alembic upgrade head`, `migrations up to date`. A `Running upgrade` line between them names
the migration that ran. A migration failure stops the container by design, rather than
serving against a schema the code does not match.

## Step 5: Verify

```bash
# which commit is actually running, on both services
for s in backend frontend; do
  img=$(docker inspect --format '{{.Config.Image}}' orcastra-dashboard-$s)
  printf '%-9s %s  %s\n' "$s" "$img" \
    "$(docker image inspect "$img" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')"
done
```

The two must print the **same** commit. They did not for one earlier release, where the
published backend and frontend under a single tag had been built eight days apart.

```bash
docker exec orcastra-dashboard-postgres \
  psql -U orcastra -d orcastra_dashboard -tAc 'SELECT version_num FROM alembic_version'

curl -s http://127.0.0.1:8765/        | python3 -m json.tool   # version, build_commit, build_date
curl -s http://127.0.0.1:8765/health  | python3 -m json.tool
docker compose -f docker-compose.prod.yml ps                   # every service healthy
```

Then sign in as each role that can reach the features you care about and confirm the pages
load. A healthy container is not a working dashboard.

## Step 6: Update monitoring

| Signal | Where | What it means |
|--------|-------|---------------|
| `temp_storage.degraded` | `GET /health` | True once the backend's tmpfs passes 80 percent. `/health` deliberately keeps returning HTTP 200 while degraded, because reporting unhealthy would restart the container, and the restart clears the tmpfs and hides the fault. Alert on the JSON field, never on the status code. |
| `temp_storage.percent_used` | `GET /health` | Graph it. At 100 percent the backend cannot write LXD credentials and answers `503 BACKEND_TEMP_STORAGE_FULL`. |
| HTTP 503 on cluster routes | access logs | An unreachable cluster now answers 503 where it used to answer 500, 404 or 400 depending on the endpoint. Any alert keyed on 500 will stop firing for real cluster outages, and any alert keyed on 5xx will fire more. Update both. |
| `BACKEND_TEMP_STORAGE_FULL` | response `code` | Says the fault is your backend's own disk, not a hypervisor. The two used to be indistinguishable, which once reported an entire healthy fleet as offline. |
| HTTP 429 on ordinary console use | access logs | `RATE_LIMIT_REQUESTS` is a deployment-wide budget, keyed on client IP. The counters are in memory, so each worker would otherwise enforce the whole number; the middleware divides it by `WEB_CONCURRENCY` to make the configured value mean what it says in aggregate. The number to size against is what one client gets, which is this value **divided by** `WEB_CONCURRENCY`, because a browser's keep-alive connection pins it to one worker. At 500 with 4 workers that is 125 a minute per operator, and one operator driving the console was measured at roughly 286. To give a single operator N a minute, set N times `WEB_CONCURRENCY`. A `.env` from a template published before v1.0.0-RC4 carries 100, which is 25 for a pinned client. Behind NAT the pressure runs the other way, since every operator shares one bucket. |
| autoheal restarts | `docker logs orcastra-dashboard-autoheal` | A restart here means a container was passing its process check while failing its healthcheck. Investigate it, do not just note it. |

## Rolling back

Pin the previous tag in `.env`, pull, and recreate. Three things a manual `pull` and `up -d`
will get wrong, and which any rollback tooling has to handle.

**The schema can be ahead of the image you are going back to.** The entrypoint runs
`alembic upgrade head` under `set -e`, and alembic cannot resolve a revision the image does
not ship, so the container dies before the API starts. `db_bootstrap` leaves an
already-stamped database alone by design and does not rescue this. It presents as a crash
loop, not as a downgrade. The script refuses rather than guessing, and `--skip-migrations`
`RUN_MIGRATIONS=false` is the escape hatch when the migrations in between are
forward-compatible, which `015_session_audit_bigint` is: it only widens three counters to
bigint and older code reads a bigint column without complaint.

**The cache has to be flushed.** Bodies written by the newer build carry fields the older
models reject, and the hardware models are declared `extra="forbid"`, so an older process
inflating one of those entries answers 500 for as long as it lives. The script clears the
affected namespaces and leaves the Vault namespace alone.

**A rollback is not symmetric.** The frontend and backend of a release can be many commits
apart in how much they change, so going back one release can be a far larger regression on
one side than the other. Check the release notes.

---

## Release notes

| Release | Schema | Compose change | Operator-visible behaviour change |
|---------|--------|----------------|-----------------------------------|
| 1.0.0-RC4 | `015_session_audit_bigint`. Widens three `session_audit_logs` counters from int to bigint, under a brief `ACCESS EXCLUSIVE` lock. The preflight prints the row count so you can judge the window. Sessions moving more than 2 GiB used to overflow on insert, and because the disconnect row is written fire-and-forget, those sessions were left with a connect row and no disconnect row. | Six optional `MAP_TILE_*` variables in the frontend service. Nothing else. | 503 when `AUTH_ENABLED=true` and `AUTHENTIK_ISSUER` is empty, where older builds served a fabricated Admin. Unreachable clusters answer 503 across roughly 30 endpoints. New `BACKEND_TEMP_STORAGE_FULL` code. `/health` gained `temp_storage`. `NodeStatus.environment` is allowlisted to 23 keys, so it no longer serves certificate PEM and fingerprints on a page any role can reach. GPU card fields renamed and `uuid` no longer serialized. Cluster ids must match `^[a-zA-Z0-9][a-zA-Z0-9._-]*$`. Live console sessions are now listed from any worker, which needs Redis; without it the sessions page degrades to a local view and says so. No dependency was added, removed or upgraded. |
