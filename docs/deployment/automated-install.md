# Automated Install

**One command on an LXD host builds the whole four-instance deployment: the instances,
their private addresses, SSH access, per-instance firewalls, Authentik, Vault, OpenSearch,
the Orcastra CMP, the port forwards browsers use, and a watchdog that unseals Vault after a
restart.** It ends by checking every layer and prints where everything is. The
[manual guides](index.md) describe the same deployment step by step and stay the reference
for understanding or customizing each component.

---

## Before you start

The installer runs on the LXD host itself, as root.

| Requirement | Details |
|---|---|
| Host OS | Ubuntu 22.04 or 24.04. Other distributions get a warning and are untested |
| LXD | 5.0 or newer, initialized (`lxd init`), with a storage pool and a managed bridge. The installer can create a new pool or bridge for you |
| LXD cluster | Supported, MicroCloud included, from installer 1.0.0-RC4. Run it on the member that should hold the instances, see [LXD clusters and MicroCloud](#lxd-clusters-and-microcloud) |
| Python | 3.8 or newer (`python3`), standard library only, nothing to `pip install` |
| Host tools | `ip`, `ss`, `openssl`, `ssh`, `ssh-keygen` |
| Virtual machines | `/dev/kvm` on the host. Without it, choose container instances |
| Host kernel | One that carries the fix for [CVE-2026-99999](../operations/security-advisories.md#cve-2026-99999-guest-to-host-flaw-in-the-host-kernels-ipv4-checksum-path) when the host runs virtual machines. Preflight warns about an unpatched kernel but does not stop the install |
| Outbound internet | From the instances, for Ubuntu packages, Docker images (Docker Hub, `ghcr.io`) and the HashiCorp and Fluent Bit apt repositories |

Sizing is chosen in the wizard:

| Profile | orca-authentik | orca-vault | orca-opensearch | orca-cmp | Total |
|---|---|---|---|---|---|
| compact | 2 vCPU, 3 GiB, 20 GiB | 1, 1, 10 | 2, 6, 40 | 2, 4, 30 | 7 vCPU, 14 GiB, 100 GiB |
| production | 2, 4, 40 | 2, 2, 20 | 4, 16, 100 | 4, 8, 60 | 12 vCPU, 30 GiB, 220 GiB |
| custom | asked per instance, with a floor per component | | | | |

Production follows the [prerequisites](../getting-started/prerequisites.md). Compact fits a
host with 16 GB of RAM. The wizard refuses a plan that does not fit the host's memory or a
`dir` pool's free space, and warns when CPUs are overcommitted.

## Run it

```bash
curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get-full.sh | sudo bash
```

The bootstrap downloads the installer from the GitHub release, checks its SHA-256 against the
value pinned in the script (it refuses to run when the check fails), and starts it. Questions
are read from your terminal, so this works through the pipe.

The wizard asks, offering a default for each:

1. instance type: `vm` (default when `/dev/kvm` exists) or `container`
2. storage pool: an existing pool, or a new name (then driver and size). On an LXD cluster
   only an existing pool is accepted. A CephFS pool is refused everywhere, because it holds
   shared file volumes and no instance disks
3. network: a new dedicated bridge by default (`orcastrabr0`, then its gateway and prefix, defaulting to a free `10.x.0.1/24`), or an existing managed bridge
4. a private IPv4 for each instance, defaulting to `.11` to `.14` in that subnet
5. sizing profile
6. the host address browsers will use, chosen from the addresses on the host
7. the host port for each web endpoint (Authentik 9000, CMP 4321, CMP API 8765, OpenSearch Dashboards 5601)
8. the admin email, and whether to type the `akadmin` password or let the installer generate one
9. whether to serve the web endpoints on public HTTPS hostnames through a Cloudflare Tunnel
   (needed to list on OrcaHub, see [Public domain](#public-domain-cloudflare-tunnel)), and if so
   the hostnames and the tunnel token; then whether to connect OrcaHub federation now
10. the Orcastra CMP release: `latest` (1.0.0-RC4-hotfix3), 1.0.0-RC4-hotfix2 or 1.0.0-RC4-hotfix1. Earlier
   releases are not offered, because their frontend healthcheck follows the sign-in redirect
   to the public URL and the frontend restarts in a loop. When Docker Hub carries a newer
   release than the installer knows, the wizard says so

The installer checks that the chosen release's images exist on Docker Hub before it starts.

Every answer is checked against the live host before anything is created. An address already
leased on the bridge or answering ping, a port something on the host already listens on, a
port already forwarded to another workload, or a subnet that overlaps an existing route is
rejected with the reason, and the question is asked again. A summary of the plan comes last,
and nothing changes until you confirm it.

!!! warning "Plain HTTP"
    The web endpoints are served over plain HTTP on the host address. Keep them on a trusted
    network or behind a VPN, and do not publish that address to the internet as it is.

## Unattended install

Every setting has one key that works as a flag, in an answer file, or as an environment
variable. A flag wins over the answer file, which wins over `ORCASTRA_<KEY>` variables, which
win over the defaults. A key the installer does not know is an error, so a typo cannot be
ignored silently.

```bash title="/root/orcastra.env"
INSTANCE_TYPE=vm
STORAGE_POOL=default
NETWORK=lxdbr0
SIZING=compact
HOST_ADDRESS=192.0.2.10
ADMIN_EMAIL=admin@example.com
CMP_VERSION=latest
ASSUME_YES=yes
```

```bash
curl -fsSL https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/installer/get-full.sh \
  | sudo bash -s -- --answers /root/orcastra.env
```

| Key | Meaning |
|---|---|
| `INSTANCE_TYPE` | `vm` or `container` |
| `LXD_PROJECT` | LXD project for the instances (default `orcastra`) |
| `STORAGE_POOL`, `POOL_DRIVER`, `POOL_SIZE_GIB`, `POOL_SOURCE` | pool to use, or the settings of a new one |
| `NETWORK`, `NETWORK_SUBNET` | bridge to use, or the gateway/prefix of a new one |
| `IP_VAULT`, `IP_AUTHENTIK`, `IP_OPENSEARCH`, `IP_CMP` | private IPv4 per instance |
| `SIZING`, `CPU_<ROLE>`, `MEM_<ROLE>`, `DISK_<ROLE>` | profile, and per-instance overrides in vCPU and GiB |
| `HOST_ADDRESS` | address browsers use |
| `PORT_AUTHENTIK`, `PORT_CMP`, `PORT_API`, `PORT_LOGS` | host ports of the web endpoints |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | `akadmin` email and password (password generated when empty, and only accepted from the answer file, never as a flag) |
| `PUBLIC_DOMAIN` | base domain for HTTPS through a Cloudflare Tunnel; empty or `none` serves plain HTTP on the host address |
| `DOMAIN_CMP`, `DOMAIN_API`, `DOMAIN_SSO`, `DOMAIN_LOGS` | public hostnames, default `app.`, `api.`, `sso.` and `logs.` under `PUBLIC_DOMAIN` |
| `CLOUDFLARE_TUNNEL_TOKEN` | the tunnel's token (answer file or environment only, never a flag; kept in `secrets.json`) |
| `ORCAHUB_CLIENT_ID`, `ORCAHUB_DISCOVERY_URL`, `ORCAHUB_CLIENT_SECRET` | the OrcaHub federation client; the secret only from the answer file or environment. `none` as the client ID switches federation off |
| `CMP_VERSION` | `latest` or a release such as `1.0.0-RC4-hotfix3` |
| `IMAGE` | LXD image for the instances (default `ubuntu:24.04`) |
| `FIX_HOST_FIREWALL` | `yes`, `no` or `ask`: whether the installer may change host settings, see [Host firewalls](#host-firewalls) |

`--dry-run` runs the host checks and the wizard, prints the plan and exits without creating,
writing or pulling anything.

## What it builds

```mermaid
graph LR
    B["Browser"] -->|host:9000 / 4321 / 8765 / 5601| H["LXD host<br/>network forwards"]
    H --> A["orca-authentik<br/>Authentik"]
    H --> C["orca-cmp<br/>Orcastra CMP"]
    H --> O["orca-opensearch<br/>OpenSearch + Dashboards"]
    C -->|8200| V["orca-vault<br/>Vault"]
    C -->|9200 logs| O
    V -->|9200 audit log| O
    C -->|issuer via host address| A
```

| Instance | Runs |
|---|---|
| `orca-vault` | Vault 1.21 from the HashiCorp apt repository, integrated raft storage, and Fluent Bit 4.2 shipping the audit log |
| `orca-authentik` | Authentik 2025.10.3 (server, worker, PostgreSQL) in Docker |
| `orca-opensearch` | OpenSearch and OpenSearch Dashboards 3.5.0 in Docker |
| `orca-cmp` | the release `docker-compose.prod.yml`: backend, frontend, PostgreSQL, Redis, Fluent Bit, autoheal |

The instances live in their own LXD project and use an `orcastra` profile (root disk, NIC,
cloud-init). Each one keeps the address you chose through a static DHCP lease, with LXD's MAC
and IPv4 filtering on its NIC so no instance can send with another one's address, starts on boot
(Vault and the stores before the CMP), and carries a `user.orcastra.role` key. Instances are
created one at a time, with `security.protection.delete`, so LXD refuses to delete them from
any client, a CMP that manages this host included, until uninstall clears the flag. LXD only
changes that flag on a stopped VM: on a deployment from an older installer the install and
verify phases print the commands to protect each VM in a maintenance window.

After starting an instance the installer waits for its cloud-init to finish, up to 45 minutes,
and prints a progress line every five minutes. A first boot on a slow host or package mirror
can take more than 20 minutes. Errors from cloud-init stop the run when the instance is new,
because its first boot did not set it up. On an instance an earlier run already set up, for
example during `--repair`, they only print a warning.

Configuration follows the manual guides, and the blocks the guides ask you to type (Vault
policy, OpenSearch security files, pipeline, index templates, ISM policies, Fluent Bit files,
the CMP `.env`) are taken from those pages, so the two paths stay identical. Authentik is
configured through its API instead of the admin UI: the role groups, `akadmin` in
`role_admin`, the OAuth2 provider and application with the exact `orcastra-dashboard` slug,
and the token the CMP uses for role sync.

### Where it differs from the manual guide

| Area | Installer | Why |
|---|---|---|
| Vault storage | integrated `raft` | the guide's `vault.hcl` has no storage stanza |
| Vault dashboard token | periodic (30 days), renewed by the watchdog | a token created with `-ttl=0` gets the 768h default and expires after 32 days |
| OpenSearch certificates | created on the host exactly as in VM 3 Step 5, with the host address in the node certificate in place of `LOGS_DOMAIN` | the CA key stays on the host, under `pki/`, and never enters an instance |
| CMP Fluent Bit CA | mounted by a small `docker-compose.orcastra.yml` next to the release compose | the release `docker-compose.prod.yml` does not mount `orcastra-logging-ca.pem` yet |
| Dashboards session cookie | `opensearch_security.cookie.secure: false`, unless a public domain is configured | Dashboards is served over plain HTTP on the host address, and a secure cookie never reaches the browser over HTTP, so the login would loop |
| Cloudflare connector | in `orca-cmp` as a sandboxed systemd unit reading the token from a 0600 file, pinned cloudflared release, denied access to Vault and the host | the guide's `cloudflared service install <token>` puts the token on the service's command line, runs it as root and installs whatever version the apt repository carries |
| Authentik trusted proxies | `orca-cmp` (the tunnel connector) and the host only | Authentik's default trusts every private range, so any LAN client could choose the address Authentik records |
| Plain-HTTP forwards in domain mode | none | the guide keeps the IP ports as a fallback; they would bypass Cloudflare rules |
| Dashboards sign-in through Authentik | not configured, sign in with `admin` | VM 3 Step 13 is optional and written for HTTPS domains (`LOGS_DOMAIN`, `AUTHENTIK_DOMAIN`), so follow it by hand once both have one |
| Role-sync token | a service account limited to reading users and groups and moving users between the three role groups | the guide uses an `akadmin` token |
| PostgreSQL and Redis on orca-cmp | bound to `127.0.0.1` | published ports bypass the guest firewall |
| Trusted proxies | loopback and the Docker networks only | the guide trusts every private range, so any LAN client could set its own client IP |
| Fluent Bit on orca-vault | Fluent Bit's signed apt repository, pinned version | the guide pipes an install script from `master` |
| Authentik | pinned version, no Docker socket in the worker, only port 9000 published | the guide downloads the latest upstream compose |

## Network, firewall and SSH

Browsers reach the four web endpoints on the host address through LXD network forwards (in
[domain mode](#public-domain-cloudflare-tunnel), through the Cloudflare Tunnel instead, and
nothing is published on the host address). When that address already has a forward (another
workload), the installer only adds its own ports to it and records exactly which ones. Vault is
never forwarded.

The issuer URL has to be the same for the browser and for the CMP's own containers, so
`orca-cmp` carries a NAT rule that sends traffic for the host address and Authentik port
straight to `orca-authentik`. The CMP reaches the Authentik API, Vault and OpenSearch on their
private addresses.

Each instance runs an nftables firewall (`orcastra-firewall.service`) that trusts only its own
loopback and Docker interfaces and drops new inbound connections on every other interface
except:

| Instance | Allowed in |
|---|---|
| all | SSH from the host only |
| orca-vault | 8200 from orca-cmp and the host |
| orca-opensearch | 9200 from orca-vault, orca-cmp and the host, 5601 from anywhere |
| orca-authentik | 9000 from anywhere |
| orca-cmp | 4321 and 8765 from anywhere |

The rules also cover ports Docker publishes, so a container started later with `-p` is not
reachable unless it is on this list.

The rules fail closed. Docker and Vault require the firewall unit, so they do not start when
the rules could not be loaded, and a network interface that appears later is filtered like the
first one. The host itself is the management point: its bridge address may reach SSH, Vault and
OpenSearch, so keep root on the LXD host to the people who run the platform.

SSH works from the host only. The installer creates a key in `/var/lib/orcastra/ssh/`, puts
the instances' host keys in a dedicated `known_hosts` file (read through `lxc exec`, so the
first connection is already verified), and writes `~/.ssh/config.d/orcastra.conf` with an
`Include` at the top of `~/.ssh/config`:

```bash
ssh orca-vault
ssh orca-cmp
```

Password login is refused and the instances cannot SSH to each other. From a laptop, jump
through the host after adding your public key to the `ubuntu` user of the instance:

```bash
ssh -J you@<host-address> ubuntu@<instance-ip>
```

### Host firewalls

Three host settings break the deployment, and the installer checks for each before creating
anything:

| Finding | What the installer adds |
|---|---|
| iptables `FORWARD` policy `DROP` (set by Docker on the host) | accept rules for the instance bridge in Docker's `DOCKER-USER` chain, kept by `orcastra-host-forward.service` |
| ufw active with a routed deny policy | `ufw allow in on <bridge>` and `ufw route allow` rules for the bridge (without them the instances get no DHCP address) |
| `vm.max_map_count` below 262144, container instances only | `/etc/sysctl.d/60-orcastra.conf` with 262144, which OpenSearch needs because containers share the host kernel |

It lists what it found and asks before changing anything. `FIX_HOST_FIREWALL=yes` applies the
changes in an unattended run, `FIX_HOST_FIREWALL=no` stops the install so you can make them
yourself. Uninstall reverts them (a lowered `vm.max_map_count` returns to its old value at the
next reboot).

## LXD clusters and MicroCloud

The installer runs on a member of an LXD cluster, a MicroCloud included. Run it as root on the
member that should hold the control plane; preflight names that member in its `LXD cluster`
row and stops when the member is not `Online` (an evacuated member, for example).

All four instances are created on that member. Their bridge exists only on that node, the
member is the SSH bastion for the instances, and the forwards listen on its address, so the
deployment cannot be spread over the cluster. Everything else follows from that:

| Topic | On a cluster |
|---|---|
| Storage pool | must already exist. On MicroCloud, `local` keeps the instance disks on the member's own disk, so the control plane keeps running when Ceph does not. `remote` keeps them on Ceph, replicated across members, and the control plane stops whenever Ceph stops. `remote-fs` (CephFS) cannot hold instance disks |
| New bridge | LXD defines a cluster network on every member, so `orcastrabr0` appears on all of them and every member must be reachable when it is created. Only the chosen member's copy carries traffic. The wizard checks the subnet against this member's routes only, so pick one that is free on every member |
| Evacuation | the instances carry `cluster.evacuate=stop`. `lxc cluster evacuate <member>` stops the control plane instead of moving it to a member where its bridge and forwards do not exist, and `lxc cluster restore <member>` starts it again. Plan maintenance of that member as CMP downtime |
| Registering the cluster in the CMP | the CMP then lists its own instances (project `orcastra`). They are protected from deletion, see [What it builds](#what-it-builds), but a delete attempt from the CMP stops the instance before LXD refuses it: start it again with `lxc start --project orcastra <name>` |
| Later runs | run `orcastra-full` (status, re-runs, uninstall) on the same member. State, keys and the watchdog live there, and preflight refuses to resume a deployment from another member |
| One deployment | per cluster, because preflight sees the instances of every member |

Uninstall removes the bridge it created from every member.

## Public domain (Cloudflare Tunnel)

OrcaHub buyers open your CMP from the internet, and OrcaHub registers an `https://` callback on
your Authentik hostname, so a deployment that lists on OrcaHub needs public HTTPS hostnames. The
installer serves them through a remotely managed Cloudflare Tunnel; no inbound port is opened.

What you need: a domain whose DNS is on Cloudflare, and a tunnel created in Cloudflare Zero Trust
(Networks, Tunnels, Create a tunnel, Cloudflared). Copy its token: the long value starting with
`eyJ` in the install command Cloudflare shows. Pasting the whole command line works too.

The wizard asks for the base domain and the four hostnames. Use names one level below the zone
(`app.example.com`, not `app.lab.example.com`): Cloudflare's free certificate does not cover a
deeper level. Before the summary it prints the routes to add to the tunnel under **Published
application routes** (older dashboards call them public hostnames):

| Public hostname | Service | Note |
|---|---|---|
| CMP (`app.`) | `http://localhost:4321` | |
| CMP API (`api.`) | `http://localhost:8765` | WebSockets on (the default) |
| Authentik (`sso.`) | `http://<orca-authentik IP>:9000` | |
| OpenSearch Dashboards (`logs.`) | `http://<orca-opensearch IP>:5601` | |

The connector runs in `orca-cmp`, where the manual [domain guide](../operations/domain-setup.md)
puts it, as `orcastra-cloudflared.service`. It is the pinned cloudflared release from GitHub,
checked against its SHA-256, with self-update off; the token sits in a mode 0600 environment
file and never on a command line. The installer waits for the connector to register, proves
every route from the host through Cloudflare, and names the route that is missing or wrong.

In domain mode every URL the deployment hands out follows the hostnames: the issuer and the
redirect URI in Authentik, `NEXTAUTH_URL`, `NEXT_PUBLIC_API_URL` and `CORS_ORIGINS` in the CMP,
the Dashboards session cookie (secure again) and the summary. The CMP's role sync still talks to
Authentik on its private address. Nothing is published in plain HTTP on the host address: the
tunnel is the only way in, so a Cloudflare Access or WAF rule on a hostname cannot be bypassed and
no password crosses a network in clear text. The connector runs sandboxed (its own unprivileged
user, a read-only system, no Docker socket, no deployment files) and cannot open a connection to
Vault or to the LXD host, whatever route someone adds in the Cloudflare dashboard.

!!! tip "Recommended on the public hostnames"
    Put a [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/applications/)
    application in front of the Dashboards hostname, which only operators need, and give
    `akadmin` a second factor in Authentik. Leave the CMP, API and SSO hostnames open to
    buyers.

A domain can be added to, or removed from, a running deployment: re-run the installer and answer
the question, or pass `--public-domain none`. Removing it takes the connector away; the tunnel
and its routes stay in your Cloudflare account until you delete them there.

!!! warning "Cloudflare security rules and server-side calls"
    The CMP's own containers fetch the issuer and its signing keys through the public SSO
    hostname. A WAF rule, Bot Fight Mode or a strict Browser Integrity Check that challenges
    non-browser clients breaks sign-in. The verify phase checks these calls; if it reports a
    Cloudflare block on the SSO hostname, add a rule that skips those checks for it.

## OrcaHub federation

[OrcaHub federation](../operations/orcahub-federation-sso.md) lets buyers sign in to your CMP with
their OrcaHub identity. The installer does Steps 1 to 3 of that guide through the Authentik API
and proves the result from the outside. It needs domain mode.

1. Ask OrcaHub for a federation client and give them the callback the installer prints:
   `https://<DOMAIN_SSO>/source/oauth/callback/orcahub/`.
2. Run `orcastra-full orcahub` and enter the client ID, the discovery URL and the client secret
   OrcaHub issued (or answer the same question in the wizard, or set the `ORCAHUB_*` keys).

It creates the OpenID Connect source `orcahub`, linking an existing user only by a verified
email, binds the guide's verified-email policy to both source flows with policy engine mode
ALL, and puts the **OrcaHub** button with its label on the login page. The gate is bound before
the source is enabled, and the source uses PKCE (S256) when OrcaHub's discovery document offers
it. Upstream groups are never accepted: the gate refuses a sign-in whose claims carry any group,
because Authentik would create that group locally and the CMP grants roles by group name. Local
roles stay assigned by your administrators. The phase then checks that the public login page
offers OrcaHub and that its button reaches OrcaHub with your client ID and exactly the
registered callback.

`orcastra-full orcahub` again replaces the client (it asks for the secret each time).
`orcastra-full orcahub --disable` removes the button and turns the source off; accounts created
through it stay. Switch federation off before removing the domain.

## Files and credentials

Everything the installer needs to manage the deployment is under `/var/lib/orcastra/`
(mode 0700):

| Path | Contents |
|---|---|
| `secrets.json` | every generated credential (0600) |
| `vault-init.json` | Vault unseal keys and root token (0600) |
| `credentials.txt` | URLs, `akadmin` and OpenSearch logins (0600) |
| `pki/` | the OpenSearch CA, node and admin certificates |
| `ssh/` | the operator key and `known_hosts` |
| `state.json` | the phase ledger and what was created, used by re-runs and uninstall |
| `install.log` | the run log, with every secret masked |

!!! danger "Back up the Vault keys"
    Copy `vault-init.json` to offline storage right after the install. It is the only way to
    unseal Vault if the host is lost, and losing it means losing every secret Vault holds.

No secret is ever passed on a command line, in the host or in the instances: values travel
on standard input or in 0600 files, and the instances' cloud-init holds only the public SSH
key.

## Vault watchdog

Vault seals itself every time it restarts. The CMP keeps serving the cluster list it read at
its own start, so a sealed Vault breaks cluster registration and certificate issuance
silently. `orcastra-maintain.timer` runs every minute on the host: it unseals Vault with the
keys in `vault-init.json`, restarts the CMP backend so it reloads the clusters, and renews the
dashboard token before it runs low. Once an hour it also removes the replica from any
OpenSearch index a plugin created with one, which on a single node would turn the cluster
yellow (the loop in VM 3 Step 10, run for you). Its actions go to the journal:

```bash
journalctl -u orcastra-maintain.service
```

## Day-2 commands

The installer stays on the host as `orcastra-full`:

| Command | What it does |
|---|---|
| `orcastra-full status` | instances, container health, Vault seal state, token TTL, certificate expiry |
| `orcastra-full verify` | the full end-to-end check, the same as the last install phase |
| `orcastra-full unseal` | unseal Vault by hand (prompts for keys when they are not on the host) |
| `orcastra-full credentials` | print URLs and logins |
| `orcastra-full orcahub` | connect OrcaHub federation, or `--disable` it, see [OrcaHub federation](#orcahub-federation) |
| `orcastra-full uninstall` | remove the deployment (asks you to type `orcastra`) |
| `orcastra-full install` | re-run, resume, or apply a new host address or ports |

## Re-runs and changes

Run the same command again at any time. Finished phases are skipped, and a run that stopped
part way resumes where it stopped. `--repair` re-runs every phase and changes nothing when
nothing needs changing. A newer installer also replaces the `orcastra-full` command and the
watchdog on the host, so day-2 commands always match the installer that last ran.

The host address, the four host ports and the public domain can change: re-run with the new
values and the installer updates the issuer, the redirect URI, the CMP URLs, the NAT rule, the
forwards and the tunnel connector.
Settings the instances were built with (instance type, project, pool, network, addresses,
sizes, CMP release) cannot change this way, and the installer refuses rather than leave the
deployment half changed. Uninstall and install again to change them, or resize an instance
with `lxc config set` yourself.

## What the last phase checks

The verification phase (and `orcastra-full verify`) fails the run when any of these fails:

- the four instances run with their addresses (and a warning when one is not protected from deletion), and `ssh` works to each
- the firewall matrix holds, including a probe container published on an unlisted port
- Vault is unsealed on raft, and the dashboard token is periodic, renewable and not root
- OpenSearch is green with no unassigned shard, over TLS verified by the private CA, with the five ISM policies and the dashboards, and no demo certificate
- in domain mode, the Cloudflare connector is connected
- every public URL answers (through the forwards, or through Cloudflare in domain mode), and the issuer seen there matches the CMP's
- a sign-in from the CMP redirects to Authentik's authorize endpoint
- from inside the CMP containers: Vault with the dashboard token, the Authentik API with the role-sync token, and the issuer through the NAT rule (or the public hostname in domain mode)
- the Vault audit log and the CMP logs arrive in OpenSearch

## Uninstall

```bash
orcastra-full uninstall
```

It clears the deletion protection, then deletes the four instances and all their data, the
forwards or ports it added, the profile and project, and the pool and bridge only when it created
them and nothing else uses them (on a cluster, the bridge goes from every member). It
also removes the timer, the `orcastra-full` command, the SSH entries and any host firewall
rules it added. `--keep-secrets` keeps `/var/lib/orcastra` under a dated name instead of
deleting it.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `LXD is slow to answer` in the log | the LXD API took over two minutes on one call. The installer retries, but a host whose LXD log shows database timeouts needs attention first |
| `Image pull failed ... rate limit` | Docker Hub's anonymous pull limit. Wait, or run `docker login` inside the instance, then re-run |
| `has no working DNS or internet egress` | NAT on the bridge, or a host firewall. See [Host firewalls](#host-firewalls) |
| `cloud-init did not finish cleanly on <instance>:` with nothing after the colon | installer 1.0.0-RC4 or earlier stopped without the reason on any non-zero exit of `cloud-init status --wait`, including a status read too early in the boot and a boot that only had warnings. Fixed in 1.0.0-RC5. With an older installer, run `lxc exec --project orcastra <instance> -- cloud-init status --long`. When it says `done` (warnings are fine), run the one-liner again: it resumes. When it lists errors, see the next rows |
| `cloud-init did not finish on <instance> within 45 minutes` | a slow package mirror or host. Follow it with `lxc exec --project orcastra <instance> -- tail -f /var/log/cloud-init-output.log`, then run the installer again once `cloud-init status` there says `done` |
| `cloud-init finished with errors on <instance>` or `cloud-init unit(s) ... failed` | the first boot of a new instance did not complete. The message prints the commands that show why. Fix the cause (often the network or a package mirror), delete that instance with the commands the message prints, and run the installer again: it creates the instance again |
| `Vault is already initialized but its unseal keys are not on this host` | restore `vault-init.json`, or delete `orca-vault` and re-run |
| `These settings cannot change once instances exist` | see [Re-runs and changes](#re-runs-and-changes) |
| `Network driver "physical" does not support forwards` in the wizard | installer 1.0.0-RC3 or earlier on a MicroCloud, whose OVN uplink is a managed physical network. Fixed in 1.0.0-RC4: run the one-liner again, it resumes |
| `Existing deployment ... on member <name>` | the deployment lives on another cluster member. Run the installer there, see [LXD clusters and MicroCloud](#lxd-clusters-and-microcloud) |
| `LXD cluster ... (Evacuated)` in preflight | restore the member with `lxc cluster restore <member>` first |
| `cloudflared did not connect tunnel ...` | a revoked or mistyped token, or egress to Cloudflare blocked (TCP 443 and UDP 7844 from `orca-cmp`). `journalctl -u orcastra-cloudflared` in `orca-cmp` shows which |
| `https://<host> does not reach ...: the hostname does not resolve` | the published application route for that hostname is missing in the tunnel |
| `... Cloudflare answered 404` or `530` | the route points at another hostname, or its service URL is wrong; compare with the table the installer prints |
| `... Cloudflare blocked the request (error code: 1010)` | a WAF, Bot Fight Mode or Browser Integrity Check rule; skip it for the hostname |
| `OrcaHub federation needs a public HTTPS domain` | set up [domain mode](#public-domain-cloudflare-tunnel) first, or run `orcastra-full orcahub --disable` before removing the domain |
| `OrcaHub sign-in must not carry group claims.` | OrcaHub sends a `groups` claim; their provider must use the no-groups profile mapping, see the federation guide |
| `OrcaHub sign-in requires a verified email address.` | the OrcaHub account's email is not verified there, or the provider does not send a Boolean `email_verified` |
| A login loop or an issuer error after changing the host address | re-run the installer with the new address so every URL is updated together |
