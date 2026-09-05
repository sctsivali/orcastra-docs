# Capabilities

**A capability is a family of endpoints an installation may call. Ten exist. Five of them reach inside a running instance and are treated differently everywhere.**

---

## The ten

| Capability | What it permits | Marked | Reaches inside a guest |
|---|---|---|---|
| `inventory.read` | Read clusters, projects, instances and region metadata | no | no |
| `lxd.proxy` | Send operations to a cluster's LXD API through the proxy | no | no |
| `storage.upload` | Upload disk images and ISO volumes | no | no |
| `org.sync` | Create and update organizations and their members | Writes access control | no |
| `tenant.provision` | Create and revoke tenant access policies | Writes access control | no |
| `instance.exec` | Run commands inside an instance, as root unless a user is named | Reaches inside the guest | yes |
| `instance.files` | Read and write files inside an instance | Reaches inside the guest | yes |
| `desktop.view` | Capture the screen of an instance's graphical session | Photographs the user's screen | yes |
| `desktop.input` | Move the mouse and type into an instance's graphical session | Acts as the logged-in desktop user | yes |
| `desktop.clipboard` | Read the clipboard of an instance's graphical session | Reads whatever the user last copied | yes |

Read the vocabulary from the API rather than copying this table into your code:

```bash
curl -sS "$ORCASTRA_API_URL/api/v1/extensions/vocabulary" \
  -H "Authorization: Bearer <YOUR_DASHBOARD_TOKEN>"
```

It is served rather than published as a constant so a client cannot offer something a deployment does not enforce.

## Why the guest five are separate

`instance.exec` runs a command in somebody's container, as root by default. `instance.files` reads any path in it. The desktop three act on a session a person is currently using.

`desktop.input` is priced above `instance.exec` on purpose, and the reason is worth stating because it is easy to assume the opposite. Exec runs in a fresh non-interactive context. Typing into a logged-in session inherits everything that session already has open: an authenticated browser, an unlocked keyring, a live ssh-agent, a sudo timestamp that has already been validated. It is a larger power, not a lateral one.

Three consequences follow.

An installation asking for any of the five is refused unless the organization has already turned guest access on. That decision belongs to the organization whose instances they are, and it is made once in the organization's settings rather than inside a dialog somebody is clicking through.

`desktop.input` needs its own permission on top of viewing, because an organization that agreed to a screenshot has not agreed to a keyboard.

None of the five is ever granted by default, by any application type, and a credential issued before capabilities existed does not inherit them.

## Elevated does not mean refused

Marked capabilities appear on the consent screen with the reason above, and an operator can approve them. The mark exists so a decision gets made rather than being one tick among ten identical ones.

!!! note "Ask for less"
    An installation only ever holds what the manifest asked for, intersected with what the operator approved. A manifest asking for `lxd.proxy` when it only reads inventory will be installed less often, and the difference is visible on the screen where somebody decides whether to trust you.
