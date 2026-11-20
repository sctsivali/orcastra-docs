"""--upgrade, --rollback and --backup for an installed Orcastra Mini.

An upgrade is one command: it checks the host, says which email each existing operator will sign
in with, backs up (a database dump, the deployment files and cold copies of the data volumes),
merges the new settings into .env without touching the existing secrets, writes the new compose and
nginx files, pulls the new images, starts them (the backend migrates the database on start), and
verifies the result. If anything after the backup fails, it restores the backup and starts the
previous release again, so the operator is never left between two releases.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from typing import Optional

from orcastra_core.errors import AbortByUser, InstallError

from . import backups, templates
from .phases import p10_vault
from .phases.p07_write import (BACKEND_UID, UPLOADS_DIR, ensure_backend_config_dir, ensure_backend_owned,
                                reown_backend_config)
from .dockerutil import compose_ps, http_probe, service_state, tail_logs, wait_for

SYNTHETIC_DOMAIN = "users.orcastra.internal"
EMAIL_MAP = os.path.join("config", "backend", "mini-email-map.json")  # /app/config in the backend
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_SECRET = 32
_SECRETS = ("LOCAL_JWT_SECRET", "AUTH_PROXY_SECRET")


def _version_tuple(text: str):
    """Sortable form of 1.2.3 and 1.2.3-RC4: a pre-release sorts below its release."""
    m = re.match(r"^\s*v?(\d+)\.(\d+)\.(\d+)(?:-([A-Za-z]+)[.-]?(\d*))?", text or "")
    if not m:
        return ((0, 0, 0), 0, "", 0)
    major, minor, patch, label, number = m.groups()
    core = (int(major), int(minor), int(patch))
    if not label:
        return (core, 1, "", 0)
    return (core, 0, label.lower(), int(number or 0))


def _step(ctx, n: int, total: int, title: str) -> None:
    ctx.log.phase(n, total, title)


# ---------------------------------------------------------------------------
# Operator emails (the database revision m002 applies the same rules)
# ---------------------------------------------------------------------------


def _label(common_name: str) -> str:
    label = (common_name or "").split(".", 1)[0].strip().lower()
    label = re.sub(r"[^a-z0-9._-]+", "-", label).strip("-.") or "operator"
    return label[:64]


def plan_emails(rows, mapped):
    """[(fingerprint, common_name, display_name, email, source)] for the existing identities."""
    counts = {}
    for _fp, _cn, display in rows:
        counts[display.strip()] = counts.get(display.strip(), 0) + 1
    taken, plan = set(), []
    for fp, cn, display in rows:
        email, source = mapped.get(fp.lower()), "chosen"
        if email is None and _EMAIL.match(display.strip()) and counts[display.strip()] == 1:
            email, source = display.strip().lower(), "display name"
            if email in taken:
                email = None
        if email is None or email in taken:
            base = _label(cn)
            email, n, source = f"{base}@{SYNTHETIC_DOMAIN}", 2, "generated"
            while email in taken:
                email, n = f"{base}-{n}@{SYNTHETIC_DOMAIN}", n + 1
        taken.add(email)
        plan.append((fp, cn, display, email, source))
    return plan


def _psql(ctx, sql: str) -> str:
    user = backups.env_value(ctx, "POSTGRES_USER", "orcastra")
    db = backups.env_value(ctx, "POSTGRES_DB", "orcastra")
    res = ctx.proc.run(ctx.compose_argv("exec", "-T", "postgres", "psql", "-U", user, "-d", db,
                                        "-At", "-F", "\t", "-c", sql), timeout=60)
    if not res.ok:
        raise InstallError("the database could not be read", remediation=res.combined.strip()[-300:])
    return res.out


def _identities_to_map(ctx):
    """RC1 identities (fingerprint, common name, display name), or None once they have emails."""
    has_email = _psql(ctx, "SELECT 1 FROM information_schema.columns WHERE table_name = 'local_identities' "
                           "AND column_name = 'email'").strip()
    if has_email:
        return None
    out = _psql(ctx, "SELECT fingerprint, common_name, coalesce(display_name, '') FROM local_identities ORDER BY id")
    return [tuple((line.split("\t") + ["", "", ""])[:3]) for line in out.splitlines() if line.strip()]


def _choose_emails(ctx, rows) -> dict:
    mapped = {}
    if ctx.flags.email_map:
        with open(ctx.flags.email_map, encoding="utf-8") as fh:
            raw = json.load(fh)
        for fp, email in raw.items():
            if not _EMAIL.match(str(email)):
                raise InstallError(f"{ctx.flags.email_map}: {email!r} is not an email address")
            mapped[fp.lower()] = str(email).strip().lower()
    plan = plan_emails(rows, mapped)
    ctx.log.info("Each existing operator will sign in as:")
    for _fp, cn, display, email, source in plan:
        ctx.log.detail(f"  {display or cn:<32} -> {email}  ({source})")
    if ctx.interactive and any(source == "generated" for *_x, source in plan):
        ctx.log.info("Generated addresses work, but organizations and keys refer to operators by email. "
                     "Enter a real address for any of them, or press Enter to keep the generated one.")
        for fp, cn, display, email, source in plan:
            if source != "generated":
                continue
            answer = ctx.prompt.ask(f"Email for {display or cn}", default=email, key="email-map")
            if answer and answer != email:
                if not _EMAIL.match(answer):
                    raise InstallError(f"{answer!r} is not an email address")
                mapped[fp.lower()] = answer.strip().lower()
        final = plan_emails(rows, mapped)
        if final != plan:
            # An address typed twice, or one another operator already holds, falls back to a
            # generated one: show what will actually happen before anything changes.
            ctx.log.info("With your answers, each operator will sign in as:")
            for _fp, cn, display, email, source in final:
                ctx.log.detail(f"  {display or cn:<32} -> {email}  ({source})")
        plan = final
    if len({p[3] for p in plan}) != len(plan):
        raise InstallError("two operators would share one email; choose different addresses")
    return {fp.lower(): email for fp, _cn, _d, email, source in plan if source == "chosen"}


# ---------------------------------------------------------------------------
# .env and deployment files
# ---------------------------------------------------------------------------


def merge_env(old: str, target_version: str):
    """The current .env with APP_VERSION moved, short secrets replaced, and every key the new
    release documents but this file lacks appended with its documented value. Nothing is removed
    and no existing value changes otherwise."""
    text = templates.set_env_value(old, "APP_VERSION", target_version) if templates.get_env_value(old, "APP_VERSION") is not None \
        else old.rstrip("\n") + f"\nAPP_VERSION={target_version}\n"
    rotated = []
    for key in _SECRETS:
        value = templates.get_env_value(text, key) or ""
        if len(value) < _MIN_SECRET:
            text = templates.set_env_value(text, key, secrets.token_hex(32)) if templates.get_env_value(text, key) is not None \
                else text.rstrip("\n") + f"\n{key}={secrets.token_hex(32)}\n"
            rotated.append(key)
    present = {k for k, _line in templates._iter_env_lines(text) if k}
    # Values that follow this deployment rather than the template's default.
    derived = {"COMPOSE_PROJECT_NAME": templates.get_env_value(text, "CONTAINER_PREFIX") or "orcastra-mini"}
    added = []
    for key, line in templates._iter_env_lines(templates.env_skeleton()):
        if key and key not in present and "<" not in line:
            added.append(f"{key}={derived[key]}" if key in derived else line)
    if added:
        text = text.rstrip("\n") + f"\n\n# Added by the upgrade to {target_version}\n" + "\n".join(added) + "\n"
    return text, [line.split("=", 1)[0] for line in added], rotated


def _write(path: str, text: str, mode: int = 0o644) -> None:
    # Created with its final mode: .env must never exist, even for a moment, readable by others.
    tmp = path + ".new"
    if os.path.lexists(tmp):
        os.remove(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def _backend_healthy(ctx) -> bool:
    state, health = service_state(compose_ps(ctx), "backend")
    return state == "running" and health == "healthy"


def _backend_refused(ctx) -> str:
    logs = tail_logs(ctx, "backend", lines=200)
    for marker in ("REFUSING TO AUTO-STAMP", "refuses to start", "Traceback"):
        if marker in logs:
            return logs[-1500:]
    return ""


CRASH_LOOP_RESTARTS = 3


def _backend_crash_looping(ctx) -> bool:
    """A backend that keeps exiting will never turn healthy; waiting out the timeout only delays
    the rollback. Docker counts the restarts of the container `up` just created."""
    res = ctx.proc.run(["docker", "inspect", "-f", "{{.RestartCount}}", f"{ctx.project}-backend"], timeout=30)
    try:
        return res.ok and int(res.out.strip() or 0) >= CRASH_LOOP_RESTARTS
    except ValueError:
        return False


def _start_vault_first(ctx, was_unsealed: bool, ui_unseal: bool) -> bool:
    """Vault starts and is unsealed before the backend, which then boots with its cluster
    registry and keys readable instead of retrying against a sealed Vault."""
    ctx.proc.run(ctx.compose_argv("up", "-d", "vault"), mutating=True, timeout=300)
    return _restore_vault_seal(ctx, was_unsealed, ui_unseal)


def _start_and_wait(ctx, timeout: int) -> None:
    up = ctx.proc.run(ctx.compose_argv("up", "-d", "--remove-orphans"), mutating=True, timeout=900)
    if not up.ok:
        # Compose only says which container failed; the backend's own log says why.
        raise InstallError("the stack did not start",
                           remediation=up.combined.strip()[-300:] + "\n\nBackend log:\n"
                           + tail_logs(ctx, "backend", lines=40)[-1500:])
    def ready():
        if _backend_healthy(ctx):
            return True
        refused = _backend_refused(ctx)
        if refused:
            raise InstallError("the backend refused the database or its settings", remediation=refused)
        if _backend_crash_looping(ctx):
            raise InstallError(f"the backend exited {CRASH_LOOP_RESTARTS} times in a row",
                               remediation=tail_logs(ctx, "backend", lines=60)[-1500:])
        return False
    if not wait_for(ctx, ready, timeout=timeout, interval=5):
        raise InstallError("the backend did not become healthy in time",
                           remediation=tail_logs(ctx, "backend", lines=60)[-1500:])


def _verify(ctx) -> dict:
    res = ctx.proc.run(ctx.compose_argv("exec", "-T", "backend", "python", "-m", "app.editions.mini.cli",
                                        "verify", "--json"), timeout=600)
    try:
        report = json.loads(res.out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise InstallError("the upgraded backend could not report its state", remediation=res.combined.strip()[-500:])
    head = ctx.proc.run(ctx.compose_argv("exec", "-T", "backend", "alembic", "current"), timeout=120)
    if "(head)" not in head.out:
        raise InstallError("the database is not at the release's schema", remediation=head.combined.strip()[-400:])
    if not report["audit"]["valid"]:
        raise InstallError("the audit chain does not verify after the upgrade",
                           remediation=str(report["audit"].get("note")))
    ctx.cfg["https_bind"] = backups.env_value(ctx, "HTTPS_BIND", "0.0.0.0")
    code = http_probe(f"https://{ctx.edge_host}:{ctx.https_port}/api/v1/auth/cert-status", timeout=10)
    if code != 200:
        raise InstallError(f"the HTTPS edge did not answer the sign-in status (got {code})")
    return report


def _vault_sealed(ctx) -> Optional[bool]:
    """True or False from `vault status`; None when Vault does not answer."""
    st = p10_vault._status(ctx)
    return None if st is None else bool(st.get("sealed", True))


def _restore_vault_seal(ctx, was_unsealed: bool, ui_unseal: bool) -> bool:
    """Leave Vault as the upgrade found it. Restarting Vault seals it, and while it is sealed no
    cluster can be reached. Returns True when Vault ends unsealed."""
    if not was_unsealed:
        return False
    wait_for(ctx, lambda: _vault_sealed(ctx) is not None, timeout=120, interval=3)
    if _vault_sealed(ctx) is False:
        return True
    persisted = p10_vault._load_persisted(ctx)
    keys = list(persisted["unseal_keys_b64"]) if persisted else []
    if not keys and ctx.interactive:
        threshold = int((p10_vault._status(ctx) or {}).get("t") or ctx.flags.unseal_threshold)
        ctx.log.info(f"Vault restarted sealed. Paste {threshold} unseal keys to unseal it now, or press Enter to skip.")
        for i in range(threshold):
            key = ctx.prompt.ask(f"Unseal key {i + 1}", default="", key="unseal-key")
            if not key:
                keys = []
                break
            ctx.log.add_secret(key)
            keys.append(key)
    if keys:
        p10_vault._unseal(ctx, keys)
        if _vault_sealed(ctx) is False:
            ctx.log.ok("Vault is unsealed again.")
            return True
    where = "from Host Health in the dashboard, or " if ui_unseal else ""
    ctx.log.warn("Vault is sealed: no cluster can be reached until it is unsealed. Unseal it "
                 f"{where}on this host, once per key: docker exec -it {ctx.project}-vault "
                 "vault operator unseal -address=http://127.0.0.1:8200")
    return False


def run_upgrade(ctx) -> int:
    target = ctx.flags.version_tag or templates.DEFAULT_IMAGE_TAG
    if not os.path.isfile(ctx.env_path) or not os.path.isfile(ctx.compose_path):
        raise InstallError(f"no Orcastra Mini deployment in {ctx.install_dir}",
                           remediation="Pass --install-dir with the directory the installer used.")
    ctx.cfg["container_prefix"] = backups.env_value(ctx, "CONTAINER_PREFIX", ctx.project)
    ctx.cfg["https_port"] = int(backups.env_value(ctx, "HTTPS_PORT", str(ctx.https_port)))
    current = backups.env_value(ctx, "APP_VERSION", "1.0.0-RC1")
    total = 8
    ctx.log.info(f"Upgrading {ctx.project} from {current} to {target}.")
    if _version_tuple(target) < _version_tuple(current):
        raise InstallError(f"{target} is older than the running {current}",
                           remediation="Use --rollback to return to the release a backup was taken from.")

    _step(ctx, 1, total, "Check the host")
    free = shutil.disk_usage(ctx.install_dir).free
    need = int(backups.volumes_size_bytes(ctx) * 2.2) + 3 * 1024 ** 3
    if free < need:
        raise InstallError(f"not enough free disk in {ctx.install_dir}: {free // 2**20} MiB free, {need // 2**20} MiB needed",
                           remediation="Free space or move the install to a larger disk, then run --upgrade again.")
    if service_state(compose_ps(ctx), "postgres")[0] != "running":
        raise InstallError("the database is not running", remediation=f"Start it: docker compose -p {ctx.project} up -d postgres")
    vault_was_unsealed = _vault_sealed(ctx) is False
    ctx.log.ok(f"{free // 2**30} GiB free; the database answers; Vault is "
               f"{'unsealed' if vault_was_unsealed else 'sealed or stopped'}.")

    _step(ctx, 2, total, "Operator emails")
    rows = _identities_to_map(ctx)
    mapping = _choose_emails(ctx, rows) if rows is not None else {}
    if rows is None:
        ctx.log.ok("Operators already have their emails.")
    if not ctx.flags.assume_yes and not ctx.prompt.confirm(f"Upgrade to {target} now? The dashboard is down for a few minutes.", default=True):
        raise AbortByUser("Upgrade cancelled; nothing was changed.")

    _step(ctx, 3, total, "Back up")
    dest = backups.new_backup_dir(ctx, f"{current}-to-{target}")
    backups.dump_database(ctx, dest)
    backups.archive_files(ctx, dest)
    stop = ctx.proc.run(ctx.compose_argv("stop"), mutating=True, timeout=300)
    if not stop.ok:
        raise InstallError("the stack could not be stopped for the backup", remediation=stop.combined.strip()[-400:])
    archived = backups.snapshot_volumes(ctx, dest)
    backups.write_manifest(dest, from_version=current, to_version=target, project=ctx.project, volumes=archived)
    ctx.log.ok(f"Backup in {dest}")

    try:
        _step(ctx, 4, total, "Merge settings")
        with open(ctx.env_path, encoding="utf-8") as fh:
            merged, added, rotated = merge_env(fh.read(), target)
        _write(ctx.env_path, merged, 0o600)
        backend_dir = ensure_backend_config_dir(ctx)
        # The first release mounted all of config/ into the backend; its node display names move
        # into the directory the backend now gets.
        old_meta = os.path.join(ctx.config_dir, "node_metadata.json")
        if os.path.isfile(old_meta) and not os.path.islink(old_meta):
            os.replace(old_meta, os.path.join(backend_dir, "node_metadata.json"))
            os.chown(os.path.join(backend_dir, "node_metadata.json"), BACKEND_UID, BACKEND_UID)
        if mapping:
            # Read by the database revision inside the backend, which runs as uid 1000.
            map_path = os.path.join(ctx.install_dir, EMAIL_MAP)
            _write(map_path, json.dumps(mapping, indent=2), 0o600)
            os.chown(map_path, BACKEND_UID, BACKEND_UID)
        ctx.log.ok(f"Kept every existing value; added {', '.join(added) or 'nothing'}"
                   + (f"; replaced short {', '.join(rotated)}" if rotated else "") + ".")

        _step(ctx, 5, total, "Write the deployment files")
        # A mirror's images (--image-backend/--image-frontend) apply on an upgrade as on an install.
        _write(ctx.compose_path, templates.render_compose(target, ctx.flags.image_backend, ctx.flags.image_frontend))
        os.makedirs(os.path.dirname(ctx.nginx_conf_path), exist_ok=True)
        _write(ctx.nginx_conf_path, templates.nginx_conf())
        if not os.path.isfile(ctx.vault_hcl_path):
            _write(ctx.vault_hcl_path, templates.vault_hcl())
        uploads = backups.env_value(ctx, "UPLOADS_DIR", UPLOADS_DIR)
        os.makedirs(uploads, exist_ok=True)
        ensure_backend_owned(ctx, uploads)
        ctx.log.ok("docker-compose.yml and config/nginx/mini.conf written.")

        _step(ctx, 6, total, "Images")
        if ctx.flags.no_pull:
            ctx.log.ok("Using the images already on this host (--no-pull).")
        else:
            pull = ctx.proc.run(ctx.compose_argv("pull"), mutating=True, timeout=1800)
            if not pull.ok:
                raise InstallError("the new images could not be pulled", remediation=pull.combined.strip()[-500:])
            ctx.log.ok("Images pulled.")

        _step(ctx, 7, total, "Start and migrate")
        vault_open = _start_vault_first(ctx, vault_was_unsealed, ui_unseal=True)
        _start_and_wait(ctx, timeout=1200)
        ctx.log.ok("The backend is healthy on the new release.")

        _step(ctx, 8, total, "Verify")
        report = _verify(ctx)
        ctx.log.ok(f"{report['active_operators']} active operators; an Administrator can sign in: "
                   f"{report['administrator_can_sign_in']}; audit chain intact ({report['audit']['checked']} rows).")
    except Exception as exc:  # noqa: BLE001 - every failure after the backup is rolled back
        ctx.log.error(f"The upgrade failed: {exc}")
        ctx.log.warn("Restoring the backup and starting the previous release ...")
        # The restore lays the old files over the new ones; a file only this upgrade wrote stays.
        if os.path.exists(os.path.join(ctx.install_dir, EMAIL_MAP)):
            os.remove(os.path.join(ctx.install_dir, EMAIL_MAP))
        _restore(ctx, dest, vault_was_unsealed)
        ctx.log.warn(f"Rolled back to {current}. The backup stays in {dest}.")
        raise InstallError(f"the upgrade to {target} failed; {current} is running again with the data it had",
                           remediation=getattr(exc, "remediation", None) or str(exc)) from exc
    if mapping:
        os.replace(os.path.join(ctx.install_dir, EMAIL_MAP), os.path.join(dest, "mini-email-map.applied.json"))
    ctx.log.ok(f"Orcastra Mini {target} is running. To return to {current}: "
               f"--rollback {os.path.basename(dest)}")
    if vault_was_unsealed and not vault_open:
        ctx.log.warn("Clusters stay unreachable until Vault is unsealed (see above).")
    return 0


def _restore(ctx, src: str, vault_was_unsealed: bool) -> None:
    ctx.proc.run(ctx.compose_argv("down", "--remove-orphans"), mutating=True, timeout=300)
    backups.restore_volumes(ctx, src)
    backups.restore_files(ctx, src)
    reown_backend_config(ctx)
    # The restored release may predate unsealing from the dashboard (1.0.0-RC1 does).
    _start_vault_first(ctx, vault_was_unsealed, ui_unseal=False)
    up = ctx.proc.run(ctx.compose_argv("up", "-d", "--remove-orphans"), mutating=True, timeout=900)
    if not up.ok:
        raise InstallError("the restored release did not start", remediation=up.combined.strip()[-500:])
    if not wait_for(ctx, lambda: _backend_healthy(ctx), timeout=900, interval=5):
        raise InstallError("the restored backend did not become healthy",
                           remediation=tail_logs(ctx, "backend", lines=60)[-1200:])


def run_rollback(ctx, which: str) -> int:
    # Only an upgrade's backup holds the volumes a rollback needs; a --backup dump is restored
    # with pg_restore (see the operations guide), never half-restored here.
    found = [p for p in backups.list_backups(ctx) if backups.read_manifest(p).get("volumes")]
    if not found:
        raise InstallError(f"no upgrade backup under {backups.backups_root(ctx)}",
                           remediation="A --backup holds a database dump only: restore it with pg_restore.")
    if which in (None, "", "latest"):
        src = found[-1]
    else:
        matches = [p for p in found if os.path.basename(p) == which]
        if not matches:
            raise InstallError(f"no backup named {which}", remediation="Available: " + ", ".join(os.path.basename(p) for p in found))
        src = matches[0]
    manifest = backups.read_manifest(src)
    ctx.cfg["container_prefix"] = manifest.get("project", ctx.project)
    ctx.log.info(f"Rolling back to {manifest.get('from_version')} from {os.path.basename(src)}.")
    if not ctx.flags.assume_yes and not ctx.prompt.confirm(
            "Everything changed since that backup (operators, audit rows, clusters) is replaced by it. Continue?", default=False):
        raise AbortByUser("Rollback cancelled; nothing was changed.")
    _restore(ctx, src, _vault_sealed(ctx) is False)
    ctx.log.ok(f"Orcastra Mini {manifest.get('from_version')} is running again.")
    return 0


def run_backup(ctx) -> int:
    if not os.path.isfile(ctx.env_path):
        raise InstallError(f"no Orcastra Mini deployment in {ctx.install_dir}")
    ctx.cfg["container_prefix"] = backups.env_value(ctx, "CONTAINER_PREFIX", ctx.project)
    current = backups.env_value(ctx, "APP_VERSION", "unknown")
    dest = backups.new_backup_dir(ctx, f"{current}-manual")
    backups.dump_database(ctx, dest)
    backups.archive_files(ctx, dest)
    backups.write_manifest(dest, from_version=current, to_version=current, project=ctx.project, volumes=[])
    ctx.log.ok(f"Database dump and deployment files saved in {dest}.")
    ctx.log.info("Vault's data is not in this backup: keep vault-init.json (its unseal keys) somewhere safe, "
                 "and use --upgrade, which also takes cold copies of every volume, before changing releases.")
    return 0
