"""InstallContext: the single object threaded through every phase. Holds resolved config,
filesystem paths, the shared log/proc/state/prompt handles, and helpers for the two
command families used everywhere (docker compose, vault exec)."""
import os


class InstallContext:
    def __init__(self, *, install_dir, flags, log, proc, state, prompt,
                 interactive, dry_run):
        self.install_dir = os.path.abspath(install_dir)
        self.flags = flags
        self.log = log
        self.proc = proc
        self.state = state
        self.prompt = prompt
        self.interactive = interactive
        self.dry_run = dry_run
        self.cfg = {}        # resolved configuration (host, ports, ttls, ...)
        self.secrets = {}    # secret name -> value, in memory only
        self.preflight = []  # list of CheckResult from phase 1

    # -- paths ---------------------------------------------------------------
    @property
    def compose_path(self):
        return os.path.join(self.install_dir, "docker-compose.yml")

    @property
    def env_path(self):
        return os.path.join(self.install_dir, ".env")

    @property
    def config_dir(self):
        return os.path.join(self.install_dir, "config")

    @property
    def nginx_conf_path(self):
        return os.path.join(self.config_dir, "nginx", "mini.conf")

    @property
    def nginx_certs_dir(self):
        return os.path.join(self.config_dir, "nginx", "certs")

    @property
    def server_crt(self):
        return os.path.join(self.nginx_certs_dir, "server.crt")

    @property
    def server_key(self):
        return os.path.join(self.nginx_certs_dir, "server.key")

    @property
    def vault_hcl_path(self):
        return os.path.join(self.config_dir, "vault", "vault.hcl")

    @property
    def state_path(self):
        return os.path.join(self.install_dir, "install-state.json")

    @property
    def vault_keys_path(self):
        return os.path.join(self.install_dir, "vault-init.json")

    @property
    def bootstrap_helper_path(self):
        return os.path.join(self.install_dir, "bootstrap-admin.sh")

    @property
    def bootstrap_close_path(self):
        return os.path.join(self.install_dir, "close-bootstrap.sh")

    @property
    def admin_p12_path(self):
        return os.path.join(self.install_dir, "admin.p12")

    # -- derived config ------------------------------------------------------
    @property
    def project(self):
        return self.cfg.get("container_prefix", "orcastra-mini")

    @property
    def https_port(self):
        return int(self.cfg.get("https_port", 6969))

    @property
    def base_url(self):
        return self.cfg.get("base_url", f"https://{self.cfg.get('host', 'localhost')}:{self.https_port}")

    # -- command helpers -----------------------------------------------------
    def compose_argv(self, *args):
        """`docker compose -p <project> -f <compose> --env-file <.env> <args...>`."""
        return [
            "docker", "compose",
            "-p", self.project,
            "-f", self.compose_path,
            "--env-file", self.env_path,
            *args,
        ]

    def vault_exec_argv(self, vault_args, token: str = None):
        """In-container vault CLI with the mandatory HTTP scheme override. The token (when
        given) is passed as an exec env var; callers should mask it via secret_args."""
        env_flags = ["-e", "VAULT_ADDR=http://127.0.0.1:8200"]
        if token:
            env_flags += ["-e", f"VAULT_TOKEN={token}"]
        return self.compose_argv("exec", "-T", *env_flags, "vault", "vault", *vault_args)
