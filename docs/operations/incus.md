# Running Orcastra on Incus

Orcastra manages Incus clusters and standalone Incus servers with the same features it offers on
LXD. This page lists what differs on Incus, which Incus builds each feature needs, and what to
expect when a build is older than that.

The behaviour below was checked against a three-member Incus 7.6 cluster and a standalone server
taken from Incus 6.0.7 through 7.0.2 to 7.6.

## Registering an Incus server

Register it the same way as an LXD server. Before the certificate is trusted, Incus tells the
dashboard nothing about itself, so the registration page shows two trust commands: one for LXD
or MicroCloud and one for Incus. Run the Incus one on any member:

```bash
cat <<EOF | incus config trust add-certificate - --name "<name shown on the page>"
<certificate shown on the page>
EOF
```

Then press **Check Authentication**. The cluster type is read from Incus at that point and shown
from then on.

## Builds each feature needs

Some features are only safe on an Incus build that carries a particular set of security fixes.
On an older build they are switched off with a message that names the running build and the one
to upgrade to. Everything else works on any supported build.

| Feature | Incus feature releases | Incus 7.0 LTS | Incus 6.0 LTS |
|---------|------------------------|---------------|---------------|
| File explorer | 7.3 or later | 7.0.2 or later | 6.0.7 or later |
| My Keys | 7.6 or later | 7.0.3 or later | 6.0.8 or later |
| Migration Center | 7.6 or later | 7.0.3 or later | 6.0.8 or later |
| Image upload on a cluster shared with another organization, or by an integration key | 7.6 or later | 7.0.3 or later | 6.0.8 or later |

The 7.6 row follows the Incus security advisories published on 29 October 2026. Their fixes reached
the LTS branches after 7.0.2 and 6.0.7 were tagged, so an LTS server shows **Security update** on
its cluster card until the next LTS point release is installed. The card says so when that release
is not out yet.

Migration Center still admits a build you have reviewed yourself when it is listed in
`MIGRATION_BUILD_ALLOW`, as on LXD.

## What differs on Incus

### Images

Incus reads images from `images.linuxcontainers.org` only; Canonical's image servers publish
LXD-only metadata. When the target cluster runs Incus, the image picker browses that catalogue,
Ubuntu included, and a request naming another image server is refused before anything is sent.

### My Keys and Permissions

Incus has no identities or groups. On an Incus cluster:

- A **Standard** key is a client certificate restricted to the key's project. It has full control
  inside that project, including shell, console and file access. It cannot reach other projects or
  the server configuration.
- An **Admin** key is an unrestricted certificate: a full administrator of the cluster.
- The Identities page lists the cluster's trusted certificates and the projects each one is
  restricted to. The dashboard's own certificate and the members' server certificates cannot be
  deleted from there.
- The Groups page names Incus clusters separately and offers no group actions on them.

Keys are revoked on the cluster when they expire, on LXD and Incus alike. Incus lets a key holder
replace the key's certificate with one of their own; the dashboard still finds the key's entry by
a name the holder cannot change, and revokes it when the key expires or is deleted. If a cluster
does not answer, or does not confirm that a key was removed, the key is kept and the delete says
why.

### Projects

Incus gives a project its own networks only through OVN. On an Incus cluster without OVN, leave
**Networks** unticked when creating a project; the project then uses the default project's
networks. A cluster shared between organizations requires projects with their own networks, so a
shared Incus cluster needs OVN.

Deleting a project removes the images Incus cached for its instances, as LXD does on its own.
Images someone uploaded are never removed to make room; the delete is refused instead.

### Virtual machines

A graceful stop sent while a VM is still booting can be reported as failed by Incus even though the
VM stops. Orcastra reads the VM's state afterwards and reports the stop as done. A restart sent
before the guest handles shutdown requests still times out with a message that suggests a forced
restart.

### Migration

Copies and moves between Incus clusters work as they do between LXD clusters. A migration between
LXD and Incus is refused at preflight.

### Integration keys

Paths that reach inside a guest through the API proxy need the `instance.exec` or `instance.files`
capability on both engines. Incus adds a few such paths, among them VM memory dumps, disk exports
(NBD), UEFI variables and port forwarding, and they are covered the same way.

A key can make a cluster pull images only from the public catalogues (`images.linuxcontainers.org`,
`images.lxd.canonical.com` and `cloud-images.ubuntu.com`), from another cluster registered in the
same dashboard, or from hosts the operator lists in `KEY_IMAGE_SERVERS_ALLOW`. Creating or
rebuilding from a URL, a migration or a conversion source is refused for keys on both engines, and
so is naming a host the cluster would send to: a push migration, an image export to another
server, or an Incus backup to an S3 bucket. Move instances with Migration Center.

## Upgrading Incus

The cluster card shows the upgrade command for the Zabbly repository the cluster uses:

```bash
sudo apt update && sudo apt install --only-upgrade incus
```

If Incus was installed from your distribution's packages instead, upgrade it through the
distribution. Moving to a newer LTS or to the feature track means switching the Zabbly repository
first; the card names the newer track but does not change it for you.
