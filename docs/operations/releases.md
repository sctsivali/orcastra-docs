# Releases

This page lists every published release of Orcastra CMP, newest first, with its state. A running
dashboard reads the same list from [`manifest.json`](https://github.com/sctsivali/orcastra-docs/blob/main/docs/releases/manifest.json)
and tells its Administrators when the version it runs is out of date. To move to a newer release,
follow [Upgrading a Deployment](upgrading.md).

## States

| State | Meaning | What an Administrator sees in the dashboard |
|-------|---------|---------------------------------------------|
| Latest | The newest release. | Nothing. |
| Supported | Still supported, but a newer release exists. | A notice that a new version is available. Dismissing it hides it until the next release. |
| Deprecated | Replaced by a newer release and on its way to end of life. Plan the upgrade. | A notice on every page. It can be put off for seven days at a time. |
| End of life | No longer supported. | A notice on every page that cannot be dismissed. |

A security release fixes a security issue. A deployment that runs an earlier release sees a notice
on every page, which cannot be dismissed until it runs the security release or a later one.

## Published releases

| Release | Published | State | |
|---------|-----------|-------|---|
| [1.0.0-RC4-hotfix3](#1-0-0-rc4-hotfix3) | 2026-10-09 | Latest |  |
| [1.0.0-RC4-hotfix2](#1-0-0-rc4-hotfix2) | 2026-09-24 | Deprecated | Security release |
| [1.0.0-RC4-hotfix1](#1-0-0-rc4-hotfix1) | 2026-09-17 | Deprecated |  |
| [1.0.0-RC4](#1-0-0-rc4) | 2026-08-28 | Deprecated |  |
| [1.0.0-RC3](#1-0-0-rc3) | 2026-08-02 | End of life |  |
| [1.0.0-RC2](#1-0-0-rc2) | 2026-03-31 | End of life |  |
| [1.0.0-RC1](#1-0-0-rc1) | 2026-03-13 | End of life |  |

The digests below are the ones Docker Hub served when each release was published, so pulling by
digest gets exactly the published image.

### 1.0.0-RC4-hotfix3 {#1-0-0-rc4-hotfix3}

Published 2026-10-09. State: Latest.

```text
svlct/orcastra-dashboard:backend-1.0.0-RC4-hotfix3@sha256:02d0fcf2104e8a32cbb04fc4fcb90d337865a8f2ffc9beaf2ea8fcfc320e7093
svlct/orcastra-dashboard:frontend-1.0.0-RC4-hotfix3@sha256:90cea512383a1bb75aba7ada5995ae0c71833655f7e7df7b56773d0fe9587862
```

### 1.0.0-RC4-hotfix2 {#1-0-0-rc4-hotfix2}

Published 2026-09-24. State: Deprecated. This is a security release, so a deployment on any earlier release should move to it or a later one.

```text
svlct/orcastra-dashboard:backend-1.0.0-RC4-hotfix2@sha256:fde551e754b62b49841c098837df02acd33a1b23ffa4620ca5d746be13c95497
svlct/orcastra-dashboard:frontend-1.0.0-RC4-hotfix2@sha256:b725cd340dacc28c25bbbc6ea42ac067c406cad9bc3eb9dc0761ba2316ac1141
```

### 1.0.0-RC4-hotfix1 {#1-0-0-rc4-hotfix1}

Published 2026-09-17. State: Deprecated.

```text
svlct/orcastra-dashboard:backend-1.0.0-RC4-hotfix1@sha256:e6add7d85e1b4dc3f996c4061725da2ada3a9002139ed4e9f363e770be6273aa
svlct/orcastra-dashboard:frontend-1.0.0-RC4-hotfix1@sha256:cb28e42ad88ebcdce2ef91888c99dc862de4a94ad197ae997db9965cacf47427
```

### 1.0.0-RC4 {#1-0-0-rc4}

Published 2026-08-28. State: Deprecated.

```text
svlct/orcastra-dashboard:backend-1.0.0-RC4@sha256:6bdb949207b7f22b97591525025935b0735a22adf052189ef25554e849006d3a
svlct/orcastra-dashboard:frontend-1.0.0-RC4@sha256:bb1cd50aae3ab8e0149415147dbff24681aa359c2a77615782b01934890692a0
```

### 1.0.0-RC3 {#1-0-0-rc3}

Published 2026-08-02. State: End of life.

```text
svlct/orcastra-dashboard:backend-1.0.0-RC3@sha256:75d1fd682d43fb05428f9205f250a5e476b5a8c01cb322c3ab22390dddb31663
svlct/orcastra-dashboard:frontend-1.0.0-RC3@sha256:13b32516fca3759d893ce5b84961266f962323c0ab3b725c2ec0f43afec4b1bb
```

### 1.0.0-RC2 {#1-0-0-rc2}

Published 2026-03-31. State: End of life.

```text
svlct/orcastra-dashboard:backend-1.0.0-RC2@sha256:04af567fabc56589143d3f458ef58dbb3bc88be3d4708c8c3094c64f6f686a52
svlct/orcastra-dashboard:frontend-1.0.0-RC2@sha256:55bd9c880648b815ab20f2f3ee6d1b1f41d5a4ada8f3b4c8a3e9722383121d38
```

### 1.0.0-RC1 {#1-0-0-rc1}

Published 2026-03-13. State: End of life.

```text
svlct/orcastra-dashboard:backend-1.0.0-RC1@sha256:83d7aaafb04c1e68011ede57466c17bf1b036706537e34e82b8770f7d4d39587
svlct/orcastra-dashboard:frontend-1.0.0-RC1@sha256:4a1a53094dd3cc4a4792b37121eccdb90441a860117cff64652b26c9fcdb09d9
```

## How the dashboard checks

One backend worker reads the list twice a day:

- It requests `https://raw.githubusercontent.com/sctsivali/orcastra-docs/main/docs/releases/manifest.json`,
  and `https://docs.orcastra.io/en/latest/releases/manifest.json` when GitHub does not answer.
  Redirects are followed only between those hosts.
- The request is a plain GET with the User-Agent `Orcastra-CMP-release-check/1`. It carries no
  version, host name, address or account.
- A list is used only if it passes the checks this repository runs on every change: schema
  version 1, newest release first, exactly one latest release, links only to this documentation,
  and at most 64 KiB. A list that fails is ignored and the last good one stays in use for up to
  seven days. After that the dashboard shows no notice until it reads a good list again.
- After a failed read it tries again after 1, 2 and 4 hours, then every 12 hours.
- An Administrator can read the list right away with **Check Now** in **Settings > Dashboard Host >
  Release**, at most once a minute. The same card shows when the list was last read, where it came
  from, and the last error.

Only Administrators see the notice. Partners and Tenants never see it, and their browsers never
request the list. A build that carries no release version, or that runs a version newer than the
newest published release, shows no notice.

### Settings

Set these in the backend's environment (`.env` beside the compose file):

| Variable | Default | Effect |
|----------|---------|--------|
| `RELEASE_CHECK_ENABLED` | `true` | `false` stops the reads and the notice. |
| `RELEASE_FEED_URL` | empty | An `https` URL of your own copy of `manifest.json`, read instead of the published list. |
| `RELEASE_FEED_PRIVATE_CIDRS` | empty | Comma-separated private networks that copy may resolve to, for example `10.20.0.0/16`. Used only with `RELEASE_FEED_URL`. |

A deployment without internet access can keep a copy of `manifest.json` on a host it reaches over
`https`, set `RELEASE_FEED_URL` to it, and update the copy when a release is published.
