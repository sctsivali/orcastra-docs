"""Logging + console UI with secret redaction.

Everything (redacted) goes to a DEBUG-level file log; the console shows phase-prefixed,
severity-styled lines. Secrets are registered as they are generated and scrubbed from
both sinks, so neither the log file nor the terminal can leak them.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional


class Redactor:
    """Holds known secret strings and masks them in any text before it is emitted."""

    def __init__(self) -> None:
        self._secrets: set = set()

    def add(self, value: object) -> None:
        if value and isinstance(value, str) and len(value) >= 4:
            self._secrets.add(value)

    def scrub(self, text: str) -> str:
        if not text:
            return text
        # longest first, so a secret that contains another is masked whole
        for s in sorted(self._secrets, key=len, reverse=True):
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
    def __init__(self, log_file: Optional[str], *, verbose: bool = False,
                 color: Optional[bool] = None, name: str = "orcastra_install") -> None:
        self.redactor = Redactor()
        self.verbose = verbose
        self.color = sys.stdout.isatty() if color is None else color
        self._logger = logging.getLogger(name)
        self._logger.setLevel(logging.DEBUG)
        self._logger.handlers.clear()
        self._logger.propagate = False
        if log_file:
            os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
            self._logger.addHandler(fh)
            try:
                os.chmod(log_file, 0o600)
            except OSError:
                pass
        self.log_file = log_file

    # -- registration --------------------------------------------------------
    def add_secret(self, value: object) -> None:
        self.redactor.add(value)

    # -- styling -------------------------------------------------------------
    def _c(self, text: str, *names: str) -> str:
        if not self.color:
            return text
        return "".join(_STYLES[n] for n in names) + text + _STYLES["reset"]

    def _emit(self, console_line: Optional[str], level: int, file_msg: str) -> None:
        msg = self.redactor.scrub(file_msg)
        self._logger.log(level, msg)
        if console_line is not None:
            print(self.redactor.scrub(console_line), flush=True)

    # -- public API ----------------------------------------------------------
    def debug(self, msg: str) -> None:
        self._emit(self._c("  · " + msg, "dim") if self.verbose else None, logging.DEBUG, msg)

    def detail(self, msg: str) -> None:
        self._emit(self._c("    " + msg, "dim"), logging.DEBUG, msg)

    def info(self, msg: str) -> None:
        self._emit("  " + msg, logging.INFO, msg)

    def ok(self, msg: str) -> None:
        self._emit("  " + self._c("✓ ", "green") + msg, logging.INFO, "OK: " + msg)

    def warn(self, msg: str) -> None:
        self._emit("  " + self._c("⚠ ", "yellow") + msg, logging.WARNING, "WARN: " + msg)

    def error(self, msg: str) -> None:
        self._emit(self._c("✗ ", "red") + msg, logging.ERROR, "ERROR: " + msg)

    def phase(self, idx: int, total: int, title: str) -> None:
        bar = self._c(f"[{idx}/{total}] ", "bold", "cyan")
        print(flush=True)
        print(bar + self._c(title, "bold"), flush=True)
        self._logger.info("=== phase %s/%s: %s ===", idx, total, title)

    def banner(self, title: str) -> None:
        line = self._c("=" * 64, "cyan")
        print(line)
        print(self._c("  " + title, "bold"))
        print(line, flush=True)
        self._logger.info("##### %s #####", title)
