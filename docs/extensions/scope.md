# Cluster And Project Scope

**An installation's reach is an intersection, and it is worked out again on every call rather than stored and trusted.**

---

## The three inputs

| Input | Set by |
|---|---|
| What the manifest asked for | You, when you published |
| What the installing operator holds | Their own cluster access |
| Which clusters the organization is bound to | The deployment's operators |

The reach is all three intersected. An add-on that asked for four clusters, installed by an operator who holds two of them, into an organization bound to one, reaches one.

The consent screen shows this before anything is granted, including the clusters that were asked for and refused, so nobody is left wondering why the list is short.

## It is recomputed, not stored

The installation records what an operator consented to. What it may do is worked out from that record plus the two live facts beside it.

That matters because the facts move. An organization can stop being bound to a cluster. An operator's own access can be narrowed. Hardware can be re-registered to a different owner. If the stored grant were the authority, an installation would keep working against a cluster its organization no longer has, and on a deployment where clusters are rented that means one tenant's add-on watching another tenant's workload.

!!! note "How you notice"
    Two ways. `GET /extensions/self` always returns the current reach, so poll it rather than caching what you were told at install. And a webhook for a cluster you no longer reach is marked withheld rather than being delivered.

## Permission levels

`read`, `write` and `admin`, in that order. An installation cannot be granted a level above what its manifest declared it needs, so publishing `read` and later wanting `write` means publishing a new version, which is
[`POST /api/v1/extensions/{slug}/versions`](versioning.md#publishing-a-new-version-of-your-own-add-on).
Existing installations stay on what they agreed to until their operator approves the new one.

## Projects

A grant names projects within a cluster. The `*` wildcard means every project, and it is only available to a manifest that declared `projects: "all"`. A grant naming specific projects cannot be mixed with the wildcard.

Some resources are not project-scoped at all, notably storage pools, networks and profiles at cluster level. Changing one of those requires the wildcard, because a project-scoped grant cannot express permission over something that belongs to every project at once.

The certificate store is never reachable through the proxy, at any level.

## Timing

Suspension and uninstallation take effect on the next call. Slower drift, such as an operator losing access to a cluster, is picked up by a reconciliation pass that runs every five minutes by default. Within that window an installation may still reach a cluster its installer no longer holds, which is a deliberate trade rather than an oversight: checking it on every request would put a database join on the authentication path.

Operators who need a change to be immediate should suspend the installation, which is.
