"""Logging + console UI with secret redaction.

Everything (redacted) goes to a DEBUG-level file log; the console shows phase-prefixed,
severity-styled lines. Secrets are registered as they are generated and scrubbed from
both sinks, so neither the log file nor the terminal can leak them.
"""
import logging
import os
import sys


class Redactor:
    """Holds known secret strings and masks them in any text before it is emitted."""

    def __init__(self):
        self._secrets = set()

    def add(self, value):
        if value and isinstance(value, str) and len(value) >= 4:
            self._secrets.add(value)

    def scrub(self, text: str) -> str:
        if not text:
            return text
        for s in self._secrets:
            if s in text:
                text = text.replace(s, "***")
        return text


# ANSI styles, used only when the stream is a TTY.
_STYLES = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "cyan": "\033[36m",
}


class Log:
    def __init__(self, log_file: str, *, verbose: bool = False, color=None):
        self.redactor = Redactor()
        self.verbose = verbose
        self.color = sys.stdout.isatty() if color is None else color
        self._logger = logging.getLogger("orcastra_mini_install")
        self._logger.setLevel(logging.DEBUG)
        self._logger.handlers.clear()
        self._logger.propagate = False
        if log_file:
            os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
            self._logger.addHandler(fh)
        self.log_file = log_file

    # -- registration --------------------------------------------------------
    def add_secret(self, value):
        self.redactor.add(value)

    # -- styling -------------------------------------------------------------
    def _c(self, text, *names):
        if not self.color:
            return text
        return "".join(_STYLES[n] for n in names) + text + _STYLES["reset"]

    def _emit(self, console_line, level, file_msg):
        msg = self.redactor.scrub(file_msg)
        self._logger.log(level, msg)
        if console_line is not None:
            print(self.redactor.scrub(console_line))

    # -- public API ----------------------------------------------------------
    def debug(self, msg):
        self._emit(self._c("  · " + msg, "dim") if self.verbose else None, logging.DEBUG, msg)

    def detail(self, msg):
        self._emit(self._c("    " + msg, "dim"), logging.DEBUG, msg)

    def info(self, msg):
        self._emit("  " + msg, logging.INFO, msg)

    def ok(self, msg):
        self._emit("  " + self._c("✓ ", "green") + msg, logging.INFO, "OK: " + msg)

    def warn(self, msg):
        self._emit("  " + self._c("⚠ ", "yellow") + msg, logging.WARNING, "WARN: " + msg)

    def error(self, msg):
        self._emit(self._c("✗ ", "red") + msg, logging.ERROR, "ERROR: " + msg)

    def phase(self, idx, total, title):
        bar = self._c(f"[{idx}/{total}] ", "bold", "cyan")
        print()
        print(bar + self._c(title, "bold"))
        self._logger.info("=== phase %s/%s: %s ===", idx, total, title)

    def banner(self, title):
        line = self._c("=" * 64, "cyan")
        print(line)
        print(self._c("  " + title, "bold"))
        print(line)
        self._logger.info("##### %s #####", title)
