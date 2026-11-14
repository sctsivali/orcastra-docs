# Upgrading a Deployment

Moving an existing VM 4 deployment from one pinned release to the next. For a first
install, follow [VM 4 - Orcastra CMP](../deployment/vm4-dashboard.md) instead.

Everything on this page is the procedure, which does not change between releases. Anything
specific to a version lives in the [release notes](#release-notes) table at the bottom, one
row per release. Check that table, then follow the steps.

[Releases](releases.md) lists every published release, its state (latest, supported,
deprecated or end of life) and its image digests. The dashboard reads the same list and tells
its Administrators when the running version is out of date.

---

## Before you start

Set these once and reuse them:

```bash
cd /opt/orcastra-dashboard          # wherever docker-compose.prod.yml and .env live
VERSION=1.0.0                       # the release you are moving to
```

A deployment made by the [Full installer](../deployment/automated-install.md) runs the CMP in
its `orca-cmp` instance, in `/opt/orcastra/cmp`, as the Compose project `orcastra` with a second
file, `docker-compose.orcastra.yml`, that gives Fluent Bit the logging CA. Open a shell there
(`lxc exec orca-cmp --project orcastra -- bash`, or the `LXD_PROJECT` you chose), `cd
/opt/orcastra/cmp`, and wherever this page writes `docker compose -f docker-compose.prod.yml`,
write `docker compose -p orcastra -f docker-compose.prod.yml -f docker-compose.orcastra.yml`.
Without `-p orcastra`, Compose names the project after the directory and does not find the running
containers or their volumes. Re-running the installer does not change the CMP release.

## Step 1: Preflight

The preflight is read-only. It changes nothing and it is the only thing that will tell you,
before you pull, whether this particular deployment is going to come back up.

Fetch the compose file that belongs to the release, so the preflight can compare it against
the one you are running:

```bash
curl -fsSL -o docker-compose.prod.yml.new \
  "https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/orcastra_full_install/assets/cmp/${VERSION}/docker-compose.prod.yml"
```

The compose files are published for `1.0.0-RC4-hotfix1`, `1.0.0-RC4-hotfix2`,
`1.0.0-RC4-hotfix3` and `1.0.0`. Releases before those have no published compose file, so a
move must target one of them.

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

The seventh build of `1.0.0-RC4-hotfix1` (2026-09-17) is the first hotfix1 build that ships
a changed compose file, and the change is the frontend healthcheck: it probes the container's
own `/healthz` with a client that never follows a redirect. The earlier probe asked `/`, an
auth-gated page, and followed its redirect through the public edge, so an edge failure marked
a healthy frontend unhealthy and the autoheal sidecar restarted it in a loop. The images
alone are safe on the old file (that build keeps a loopback request's redirect on loopback),
but only the new file removes the dependency on the edge. The file is also attached to the
GitHub release with its sha256 in the release notes, for a deployment that cannot reach the
repository.

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
| HTTP 429 on ordinary console use | access logs | `RATE_LIMIT_REQUESTS` is enforced per backend worker at the full configured value, keyed on client IP (only the forced-refresh budget is shared across workers through Redis). A browser's keep-alive connection pins it to one worker, so one operator gets the configured number a minute; the deployment as a whole can admit up to that number times `WEB_CONCURRENCY`. Size against a single operator: one driving the console was measured at roughly 286 requests a minute, so the code default of 500 is the floor to keep. A `.env` from a template published before v1.0.0-RC4 carries 100, which throttles a single operator; raise it to 500. From the seventh build of `1.0.0-RC4-hotfix1` a 429 carries `Retry-After` as the seconds actually left in the window and `X-RateLimit-Scope` naming the budget (`ip`, `subnet` or `fresh`), and the console counts the wait down under the control that was refused. From 1.0.0 a request a signed-in account makes through the dashboard counts against that account (`X-RateLimit-Scope: user`), so operators behind one NAT no longer share a bucket; anonymous requests and direct API callers stay keyed on client IP. |
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

`015a_organization_owner_keys` (1.0.0-RC4-hotfix3) is not. An older release claims an
organization without its owner row, which that revision's foreign key refuses, so a rollback
across it only trades a crash loop for failing claims. Downgrade it first, from an image that
ships it (`docker exec orcastra-dashboard-backend alembic downgrade 015_session_audit_bigint`),
then roll back without `--skip-migrations`. `scripts/rollback_release.sh` refuses
`--skip-migrations` across it and changes nothing.

**The cache has to be flushed.** Bodies written by the newer build carry fields the older
models reject, and the hardware models are declared `extra="forbid"`, so an older process
inflating one of those entries answers 500 for as long as it lives. The script clears the
affected namespaces and leaves the Vault namespace alone.

**There is no way down from 1.0.0 but the dump.** 1.0.0 takes the database from
`015a_organization_owner_keys` (or `015_session_audit_bigint`) to `046_forget_deregistered_records`
in one transaction, and the downgrade steps in between were never meant for production: some
delete rows and one refuses while add-ons are installed. Going back means stopping the 1.0.0
backend, restoring the `pg_dump` taken in Step 2 into the database, and starting the earlier
release on its own compose file and `.env`. Everything written since the dump is lost.

**A rollback is not symmetric.** The frontend and backend of a release can be many commits
apart in how much they change, so going back one release can be a far larger regression on
one side than the other. Check the release notes.

---

## Upgrading to 1.0.0

Check these before recreating the containers on 1.0.0. They apply when coming from any
1.0.0-RC4 hotfix.

| Change | What to do |
|---|---|
| Every service is recreated | The 1.0.0 compose file changes all six services (log rotation, loopback ports), so `up -d` recreates Postgres, Redis, Fluent Bit and autoheal too. Their volumes stay. Stop autoheal first on a large database (`docker compose -f docker-compose.prod.yml stop autoheal`): the backend runs the 31 schema steps before it reports healthy, and autoheal restarts a backend that takes longer than about three minutes. |
| Tenants act on their own projects only | `TENANT_PERM_ENFORCEMENT_ENABLED` now defaults to `true`: a tenant can operate and open consoles only on the projects and instances its policies name, not on everything on a cluster it holds any policy for. `TENANT_PERM_ENFORCEMENT_ENABLED=false` keeps the old behaviour. |
| One rate limit per signed-in account | Page calls arrive through the dashboard frontend, which now marks the ones it authenticated from a session, and the backend counts those per account (`X-RateLimit-Scope: user`). Integration keys and anonymous callers are still counted per address. |
| Purchases bound what a buyer's keys reach | Integration keys, add-ons and snapshot schedules a buyer founder created follow the paid term of the purchase through which they reach a cluster: read only in retention (no guest access), nothing once access ends. Such a key can no longer provision or revoke tenant access on that cluster. |
| The backend renews its own Vault token | Nothing, if `VAULT_TOKEN` is a periodic token (`-period=720h`). A token created with `-ttl=0` cannot be renewed past 768 hours: replace it ([Vault guide](../deployment/vm2-vault.md#create-dashboard-token)). The daily renewal job from older guides is harmless to keep. `VAULT_TOKEN_RENEW_ENABLED=false` turns the renewal off. |
| A refused Vault token is its own state | The Vault notice, Host Health and `/health` (`vault.token.state`) say `token_refused`, and integration API keys answer 503 `api_key_store_unavailable` instead of 401 while it lasts. Alerts keyed on a 401 from the key routes stop firing for this outage. |
| Operational events | New records in `orcastra-audit-*` (`category: operations`) and in the Cluster Log. See [Operational Events](operational-events.md). |
| Postgres and Redis are published on loopback | The compose files read `POSTGRES_BIND`/`POSTGRES_HOST_PORT` and `REDIS_BIND`/`REDIS_HOST_PORT`, defaulting to `127.0.0.1`, and no longer read `POSTGRES_PORT` or `REDIS_PORT`. An env file that set those to a bare port published the store on every interface; after the upgrade it is on loopback. Anything outside the host that connected to them directly needs `POSTGRES_BIND` set on purpose. |
| Postgres gets 256 MB of shared memory | Docker gives a container 64 MB of `/dev/shm`. Postgres keeps about 1.2 MB there for every pooled connection that has run a parallel query, which happens once a table passes 8 MB, and the four backend workers hold about 80 connections. With the default, queries start failing with `could not resize shared memory segment ... No space left on device` and the API answers 500. The compose file now sets `shm_size` from `POSTGRES_SHM_SIZE` (default `256mb`); raise it if you raise Postgres `max_connections`. It applies when Postgres is recreated. |
| Container logs rotate | Every service writes json-file logs rotated at five 50 MB files, whatever the host's `daemon.json` says. Recreate the containers for it to apply. |
| Production refuses two development settings | With `DEBUG=false` the backend refuses to start when `AUTH_ENABLED=false` (every caller would be an Administrator) or when `CORS_ORIGINS` contains `*`. The log line says which. |
| Template secrets are reported | A `SECRET_KEY`, `NEXTAUTH_SECRET`, `REDIS_ENCRYPTION_KEY` or database password still at the template's `replace_me` value, or a secret shorter than 32 characters, is logged at every start and shown in Host Health (Secrets and Keys) and to Administrators on every page. Nothing refuses to start. A new `SECRET_KEY` invalidates every integration API key. |
| Client IP forwarding | The compose file now defaults `FORWARD_CLIENT_IP_HEADERS` to `false`; the hotfix compose defaulted to `true`. An `.env` that does not set it changes behaviour: behind a reverse proxy the audit trail then records the frontend container's address for every user. Set `FORWARD_CLIENT_IP_HEADERS=true` only behind a proxy that sets the headers and is listed in `TRUSTED_PROXY_CIDRS`; the env template sets it for that case. |
| The access token stays on the server | The browser no longer receives the session's access token. Its API calls go to the dashboard hostname under `/api/v1/`, and the dashboard frontend attaches the token from the session cookie. A reverse proxy or tunnel route that sends `/api/v1/` on the dashboard hostname straight to the backend must send it to the frontend instead, or every API call from the browser answers 401. The API hostname, integration API keys, MCP clients and the WebSockets are unchanged. A write whose `Origin` is another site is now refused with 403. |
| A new session cookie on https deployments | When `AUTH_URL` or `NEXTAUTH_URL` is https, the session cookie is now `__Host-authjs.session-token`, which no other host on the same domain can set or shadow. Every signed-in user signs in again once after the upgrade. Plain http deployments keep the old name. |
| Scripts need the page's nonce | Every page response carries a Content Security Policy with its own nonce, and the browser runs only scripts that carry it. A reverse proxy or CDN feature that injects scripts into the page (for example Cloudflare Rocket Loader, email address obfuscation or automatic analytics) is blocked: turn it off for the dashboard hostname. Violations are logged by the frontend container as `log_type: csp_report`. `CSP_SCRIPT_POLICY=report-only` on the frontend stops enforcing and only reports while you do. |

## Release notes

| Release | Schema | Compose change | Operator-visible behaviour change |
|---------|--------|----------------|-----------------------------------|
| 1.0.0-RC4 | `015_session_audit_bigint`. Widens three `session_audit_logs` counters from int to bigint, under a brief `ACCESS EXCLUSIVE` lock. The preflight prints the row count so you can judge the window. Sessions moving more than 2 GiB used to overflow on insert, and because the disconnect row is written fire-and-forget, those sessions were left with a connect row and no disconnect row. | Six optional `MAP_TILE_*` variables in the frontend service ([Regions Map Basemap](regions-basemap.md)). Nothing else. | 503 when `AUTH_ENABLED=true` and `AUTHENTIK_ISSUER` is empty, where older builds served a fabricated Admin. Unreachable clusters answer 503 across roughly 30 endpoints. New `BACKEND_TEMP_STORAGE_FULL` code. `/health` gained `temp_storage`. `NodeStatus.environment` is allowlisted to 23 keys, so it no longer serves certificate PEM and fingerprints on a page any role can reach. GPU card fields renamed and `uuid` no longer serialized. Cluster ids must match `^[a-zA-Z0-9][a-zA-Z0-9._-]*$`. Live console sessions are now listed from any worker, which needs Redis; without it the sessions page degrades to a local view and says so. No dependency was added, removed or upgraded. |
