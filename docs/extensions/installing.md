# Installing An Add-on

**This page is for the operator doing the installing, rather than the developer who wrote the add-on.**

---

You need the partner role. Open **Extensions** in the sidebar, or **Settings** then **Extensions**.

## Choose an organization first

An installation belongs to one organization. The same add-on installed into two organizations gets two credentials with two different reaches, and one has no visibility of the other.

## What the consent screen is asking

Six things, in order.

**Who wrote it.** The name, the publisher and the version. None of it is verified by Orcastra, so treat it the way you would treat any third-party software: install what you have a reason to trust.

**What it may call.** Every capability it asked for, with a plain description. Capabilities that reach inside a running instance are pulled out into their own block above the rest, because approving one of those is a different decision from approving a read.

**Where it may reach.** The clusters it will actually get, which is what it asked for intersected with what you hold and what this organization is bound to. Clusters it asked for and will not get are listed too, so a short list is explained rather than surprising.

**What it will be told.** The events it can subscribe to.

**What it can never do.** Run code in the dashboard, read your session, draw a panel in these pages, or reach past the granted list.

**That the credential appears once.** Have somewhere to paste it before you confirm.

If the add-on asked for guest access, the confirm stays disabled until you tick an acknowledgement. That is the one piece of friction here, and it is there because those capabilities let software act inside instances your tenants are using.

## After installing

Two secrets are shown, once. The credential authenticates the add-on to Orcastra; the signing secret lets it verify that a delivery came from Orcastra. Hand both to whoever runs the add-on.

If either is lost, rotate rather than looking for it. There is no way to read one back.

## Suspending

Suspending stops the credential working on the next call and stops deliveries, without removing anything. It is reversible, and it is the right move when something looks wrong and you want time to look at it.

## Uninstalling

Uninstalling removes the credential, the signing secret, the stored settings, every webhook subscription and the delivery log, and revokes any elevated grant issued to that credential.

What it keeps is the audit trail. Those records are how you answer what the add-on did while it was installed, so deleting them would remove the evidence at the moment it is most likely to be wanted.

!!! note "Uninstalling is not the same as the add-on being withdrawn"
    An author withdrawing an add-on does not remove it from organizations that have it installed. Uninstalling is always the operator's decision.

## What you can see afterwards

Every install, re-scope, rotation, suspension and uninstall is recorded as a high-severity administrative event naming the operator who did it, the organization it affected and the credential involved. Deliveries are visible per installation, including what was sent, what the endpoint answered and what was withheld.
