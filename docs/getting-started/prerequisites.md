# Prerequisites

Before beginning the deployment, ensure you have the following infrastructure and accounts ready.

## Infrastructure Requirements

### Virtual Machines

You need four VMs with the following minimum specifications:

| VM | Role | vCPU | RAM | Storage | OS |
|---|---|---|---|---|---|
| VM 1 | Authentik (SSO) | 2 | 4 GB | 40 GB | Ubuntu 22.04+ |
| VM 2 | Vault (Secrets) | 2 | 2 GB | 20 GB | Ubuntu 22.04+ |
| VM 3 | OpenSearch (Logging) | 4 | 16 GB | 100 GB | Ubuntu 22.04+ |
| VM 4 | Orcastra Dashboard | 4 | 8 GB | 60 GB | Ubuntu 22.04+ |

### Host Virtualization (Nested Virtualization)

Orcastra runs inside LXD/Incus instances on a host server. Two different "nesting" settings come up, and they solve different problems. Do not confuse them:

| Setting | Layer | Purpose |
|---|---|---|
| LXD `Nesting (Containers only)` | OS (LXD container) | Lets Docker run inside an LXD **container**. Configured in [LXD Configuration](#lxd-configuration) below. |
| Hardware nested virtualization | CPU / hypervisor | Lets a virtualized host run **VM-type** instances (KVM) inside itself. |

You need hardware nested virtualization when either of these is true:

- The LXD/Incus host that runs the Orcastra VMs is **itself a virtual machine** (such as a Proxmox, VMware, Hyper-V, or cloud instance) and you want to run VM-type instances on it.
- A node managed by Orcastra will host **VM-type** instances (not only system containers) for tenant workloads. That hypervisor node needs hardware virtualization, plus nested virtualization if the node is itself virtualized.

If your host is bare metal and you only run LXD system containers, the container `Nesting` setting below is enough and you can skip this.

#### Verify

Run on the host that will provide virtualization:

```bash
# CPU virtualization extensions exposed to this host? (>0 = yes)
egrep -c '(vmx|svm)' /proc/cpuinfo

# KVM nested flag (on a KVM-based host)
cat /sys/module/kvm_intel/parameters/nested 2>/dev/null   # Intel: Y or 1
cat /sys/module/kvm_amd/parameters/nested 2>/dev/null     # AMD:   Y or 1
```

!!! tip "Ubuntu has a one-shot checker"
    ```bash
    sudo apt install -y cpu-checker && kvm-ok
    ```
    A "KVM acceleration can be used" result confirms the host can run hardware-accelerated VMs.

#### Enable

If the host is virtualized, enable nested virtualization on the **outer** hypervisor, then give the Orcastra host VM a CPU type that passes the host features through.

=== "KVM / libvirt"

    On the physical KVM host (Intel shown; use `kvm_amd` on AMD):

    ```bash
    echo "options kvm_intel nested=1" | sudo tee /etc/modprobe.d/kvm-nested.conf
    sudo modprobe -r kvm_intel && sudo modprobe kvm_intel
    cat /sys/module/kvm_intel/parameters/nested   # expect Y
    ```

    Give the guest a passthrough CPU in its libvirt domain XML:

    ```xml
    <cpu mode='host-passthrough'/>
    ```

=== "Proxmox VE"

    Confirm the node has nesting on, then set the Orcastra host VM's CPU to `host`:

    ```bash
    cat /sys/module/kvm_intel/parameters/nested   # expect Y (use kvm_amd on AMD)
    qm set <vmid> --cpu host
    ```

=== "VMware"

    On the VM, enable **Expose hardware-assisted virtualization to the guest OS**, or add this line to the `.vmx` file:

    ```
    vhv.enable = "TRUE"
    ```

=== "Hyper-V"

    From an elevated PowerShell on the Hyper-V host (the VM must be powered off):

    ```powershell
    Set-VMProcessor -VMName <name> -ExposeVirtualizationExtensions $true
    ```

=== "Cloud"

    Nested virtualization support depends on the provider and instance type:

    - **Google Cloud:** available on most Intel instances when nested virtualization is enabled on the image or instance.
    - **Azure:** available on Dv3/Ev3 and newer VM sizes.
    - **AWS:** only on bare-metal (`*.metal`) instances.

    Check your provider's documentation before sizing the host.

### LXD Configuration

All LXD containers require the following security settings:

1. Navigate to **Security Policies** in the instance configuration
2. Set **Privileged (Containers only)** → `Allow`
3. Set **Nesting (Containers only)** → `Allow`
4. Save changes

!!! warning "VM 3 - Additional Host Configuration"
    The LXD **host server** (not the container) requires increased virtual memory mapping for OpenSearch:

    ```bash
    sudo sysctl -w vm.max_map_count=262144
    echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
    sudo sysctl -p
    ```

### Networking

- All four VMs must be able to communicate with each other over the LXD network
- The following ports must be accessible (via LXD port forwarding or direct access):

| Service | Port | VM | Direction |
|---|---|---|---|
| Authentik | 9000 | VM 1 | Inbound (browser + VM 4) |
| Vault | 8200 | VM 2 | Inbound (VM 4) |
| OpenSearch | 9200 | VM 3 | Inbound (VM 2 + VM 4) |
| OpenSearch Dashboards | 5601 | VM 3 | Inbound (browser) |
| Orcastra Frontend | 4321 | VM 4 | Inbound (browser) |
| Orcastra Backend | 8765 | VM 4 | Inbound (browser) |

## Software Requirements

All VMs require Docker with the Compose plugin. The Docker installation steps are included in each VM's deployment guide.

| Software | Version | Required On |
|---|---|---|
| Docker Engine | 24.0+ | VM 1, VM 3, VM 4 |
| Docker Compose Plugin | 2.20+ | VM 1, VM 3, VM 4 |
| Vault | 1.15+ | VM 2 (native install) |
| Fluent Bit | 3.0+ | VM 2 (native install) |

!!! tip "Deployment directory convention"
    On each Docker-based VM (VM 1, VM 3, VM 4), run the deployment from a dedicated `~/orcastra` directory (`mkdir -p ~/orcastra && cd ~/orcastra`). Keeping the compose file, `.env`, and Docker volumes together in one path makes upgrades and troubleshooting predictable, and gives Docker Compose a consistent project name. VM 2 (Vault) installs natively, so it has no project directory.

## Accounts & Credentials

During deployment, you will generate and collect the following credentials. **Keep them secure** - they are required across VMs.

| Credential | Generated On | Used On | Description |
|---|---|---|---|
| Authentik admin password | VM 1 | VM 1 | `akadmin` account password |
| OAuth2 Client ID | VM 1 | VM 4 | OIDC provider client identifier |
| OAuth2 Client Secret | VM 1 | VM 4 | OIDC provider client secret |
| Authentik API Token | VM 1 | VM 4 | Token for group sync (optional) |
| Vault Unseal Keys (×5) | VM 2 | VM 2 | Required to unseal Vault after restart |
| Vault Root Token | VM 2 | VM 2 | Initial root access token |
| Vault Dashboard Token | VM 2 | VM 4 | Scoped token for Orcastra |
| OpenSearch Admin Password | VM 3 | VM 3 | Admin access to OpenSearch |
| OpenSearch Dashboards Password | VM 3 | VM 3 | `kibanaserver` internal user |
| Fluent Bit Password | VM 3 | VM 2, VM 4 | Log writer service account |
| PostgreSQL Password | VM 4 | VM 4 | Database access |
| NextAuth Secret | VM 4 | VM 4 | Session encryption key |
| Redis Encryption Key | VM 4 | VM 4 | Cache encryption (Fernet) |
| Secret Key | VM 4 | VM 4 | Backend application secret |

!!! danger "Credential Security"
    Store all credentials in a secure password manager. Never commit them to version control. The deployment scripts generate strong random values - use them as-is.

## Optional Requirements

| Feature | Requirement |
|---|---|
| Custom domain (for example, `app.orcastra.io`) | Cloudflare account with DNS management |
| HTTPS via Cloudflare Tunnel | Cloudflare Zero Trust (free tier) |
| Persistent iptables rules | `iptables-persistent` package on VM 4 |

## Deployment Order

!!! tip "Follow This Order"
    Each VM depends on credentials and configuration from previous VMs:

    ```
    VM 1 (Authentik) → VM 2 (Vault) → VM 3 (OpenSearch) → VM 4 (Dashboard)
    ```

    You **cannot** deploy VM 4 without first completing VMs 1–3, as the Dashboard `.env` requires values from all three.
