"""Subprocess wrapper. List-form argv only (never shell=True with interpolated values),
secret-bearing args masked in logs, and a dry-run gate for mutating commands."""
import subprocess

from .log import Log  # noqa: F401  (type reference)


class Result:
    def __init__(self, rc: int, out: str, err: str, argv):
        self.rc = rc
        self.out = out
        self.err = err
        self.argv = argv

    @property
    def ok(self) -> bool:
        return self.rc == 0

    @property
    def combined(self) -> str:
        return (self.out or "") + (self.err or "")


def _mask(argv, secret_args):
    if not secret_args:
        return list(argv)
    sset = set(secret_args)
    return ["***" if a in sset else a for a in argv]


class Proc:
    def __init__(self, log: Log, *, dry_run: bool = False):
        self.log = log
        self.dry_run = dry_run

    def run(self, argv, *, input: str = None, env: dict = None, secret_args=(),
            mutating: bool = False, timeout: int = None, quiet_output: bool = False) -> Result:
        """Run a command, capturing output. Returns a Result (never raises on non-zero;
        callers inspect `.ok`/`.rc`). When dry-run and `mutating`, the command is skipped
        and a synthetic success is returned."""
        shown = _mask(argv, secret_args)
        if self.dry_run and mutating:
            self.log.detail("[dry-run] would run: " + " ".join(shown))
            return Result(0, "", "", argv)
        self.log.debug("run: " + " ".join(shown))
        try:
            # stdin is never inherited: a child must not read the operator's terminal or
            # the `curl | bash` pipe behind our back
            cp = subprocess.run(
                list(argv), input=input, env=env, timeout=timeout,
                stdin=subprocess.DEVNULL if input is None else None,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True,
            )
        except FileNotFoundError as exc:
            self.log.debug(f"not found: {argv[0]} ({exc})")
            return Result(127, "", str(exc), argv)
        except subprocess.TimeoutExpired as exc:
            self.log.debug(f"timeout after {timeout}s: {' '.join(shown)}")
            return Result(124, exc.stdout or "", (exc.stderr or "") + "\n[timeout]", argv)
        if cp.stdout and not quiet_output:
            # quiet_output: the command prints secret material (a hash, a file of secrets)
            self.log.debug("stdout: " + self.log.redactor.scrub(cp.stdout.strip()[:2000]))
        if cp.returncode != 0 and cp.stderr:
            self.log.debug("stderr: " + self.log.redactor.scrub(cp.stderr.strip()[:2000]))
        return Result(cp.returncode, cp.stdout or "", cp.stderr or "", argv)

    def run_interactive(self, argv, *, env: dict = None, mutating: bool = True,
                        stdin=None) -> int:
        """Run a command inheriting the terminal (stdin/stdout/stderr), for flows the user
        must drive directly - ex; `docker login` rendering its own device-code prompt.
        `stdin` lets the caller hand over /dev/tty when our own stdin is a pipe."""
        shown = " ".join(argv)
        if self.dry_run and mutating:
            self.log.detail("[dry-run] would run interactively: " + shown)
            return 0
        self.log.debug("interactive: " + shown)
        try:
            cp = subprocess.run(list(argv), env=env, stdin=stdin)
            return cp.returncode
        except FileNotFoundError:
            return 127

    def which(self, name: str):
        import shutil
        return shutil.which(name)
