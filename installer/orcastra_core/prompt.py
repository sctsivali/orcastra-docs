"""Interactive prompts that work under `curl ... | bash` and degrade cleanly to
non-interactive mode.

When the installer is piped into bash, stdin is the download stream, not the keyboard,
so questions are read from the controlling terminal (`/dev/tty`). In non-interactive mode a
question with a usable default returns it, one without raises AbortByUser so an unattended
run fails loudly instead of hanging.
"""
from __future__ import annotations

import os
import sys
from typing import Callable, List, Optional, TextIO, Tuple

from .errors import AbortByUser, ConfigError

Validator = Callable[[str], Optional[str]]


def open_tty() -> Optional[TextIO]:
    """The stream to read answers from: stdin when it is a terminal, else /dev/tty when the
    process still has a controlling terminal (the `curl | bash` case), else None."""
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            return sys.stdin
    except (AttributeError, ValueError):
        pass
    try:
        # read-only: "r+" would ask for a seekable stream, which a terminal is not, and the
        # resulting UnsupportedOperation would silently turn the run non-interactive
        fd = os.open("/dev/tty", os.O_RDONLY | os.O_NOCTTY)
        return os.fdopen(fd, "r", encoding="utf-8", errors="replace")
    except OSError:
        return None


class Prompter:
    def __init__(self, log, *, interactive: bool, assume_yes: bool = False,
                 tty: Optional[TextIO] = None) -> None:
        self.log = log
        self.interactive = interactive and (tty is not None or sys.stdin.isatty())
        self.assume_yes = assume_yes
        self._tty = tty if tty is not None else (sys.stdin if self.interactive else None)

    @property
    def tty(self) -> Optional[TextIO]:
        """The answer stream, for child processes that must talk to the operator."""
        return self._tty

    def _ask_line(self, text: str) -> str:
        sys.stdout.write(text)
        sys.stdout.flush()
        line = self._tty.readline() if self._tty is not None else ""
        if line == "":
            raise AbortByUser("No terminal input available (EOF).",
                              remediation="Run from an interactive terminal, or pass the "
                                          "answers as flags / an answer file.")
        return line.rstrip("\n")

    def _ask_hidden(self, text: str) -> str:
        """Read a line without echo from the prompt terminal."""
        import termios
        stream = self._tty
        if stream is None:
            raise AbortByUser("No terminal input available.")
        fd = stream.fileno()
        sys.stdout.write(text)
        sys.stdout.flush()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[3] &= ~termios.ECHO
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, new)
            line = stream.readline()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            sys.stdout.write("\n")
        if line == "":
            raise AbortByUser("No terminal input available (EOF).")
        return line.rstrip("\n")

    def ask(self, question: str, *, default: Optional[str] = None, key: Optional[str] = None,
            validate: Optional[Validator] = None) -> str:
        if not self.interactive:
            if default is not None:
                err = validate(default) if validate else None
                if err:
                    raise ConfigError(f"{question}: {err}",
                                      remediation=f"Set a valid value via --{key or 'flag'} "
                                                  "or the answer file.")
                self.log.detail(f"{question} -> {default} (non-interactive default)")
                return default
            raise AbortByUser(
                f"Need a value for: {question}",
                remediation=f"Provide it via --{(key or 'flag')} or the answer file, "
                            "or run interactively.")
        suffix = f" [{default}]" if default is not None else ""
        while True:
            ans = self._ask_line(f"  {question}{suffix}: ").strip()
            if not ans and default is not None:
                ans = default
            if not ans:
                continue
            err = validate(ans) if validate else None
            if err:
                print(f"    {err}", flush=True)
                continue
            return ans

    def ask_secret(self, question: str, *, validate: Optional[Validator] = None,
                   confirm: bool = True) -> str:
        """Hidden input, typed twice. Only meaningful interactively."""
        if not self.interactive:
            raise AbortByUser(f"Need a value for: {question}",
                              remediation="Provide it in the answer file or let the "
                                          "installer generate it.")
        while True:
            first = self._ask_hidden(f"  {question}: ")
            err = validate(first) if validate else None
            if err:
                print(f"    {err}", flush=True)
                continue
            if confirm and self._ask_hidden("  Repeat to confirm: ") != first:
                print("    The two entries differ, try again.", flush=True)
                continue
            return first

    def confirm(self, question: str, *, default: bool = False) -> bool:
        if self.assume_yes:
            self.log.detail(f"{question} -> yes (--assume-yes)")
            return True
        if not self.interactive:
            self.log.detail(f"{question} -> {default} (non-interactive default)")
            return default
        hint = "Y/n" if default else "y/N"
        while True:
            ans = self._ask_line(f"  {question} [{hint}]: ").strip().lower()
            if not ans:
                return default
            if ans in ("y", "yes"):
                return True
            if ans in ("n", "no"):
                return False

    def choose(self, question: str, options: List[Tuple[str, str]], *,
               default_index: int = 0) -> str:
        """options: list of (value, label). Returns the chosen value."""
        if not self.interactive or self.assume_yes:
            val = options[default_index][0]
            self.log.detail(f"{question} -> {val} (non-interactive default)")
            return val
        print(f"  {question}")
        for i, (_, label) in enumerate(options, 1):
            marker = "*" if i - 1 == default_index else " "
            print(f"   {marker} {i}) {label}")
        while True:
            ans = self._ask_line(
                f"  choose [1-{len(options)}, default {default_index + 1}]: ").strip()
            if not ans:
                return options[default_index][0]
            if ans.isdigit() and 1 <= int(ans) <= len(options):
                return options[int(ans) - 1][0]

    def pause(self, message: str) -> None:
        """Block until the operator acknowledges (used for 'save these keys')."""
        if not self.interactive:
            return
        self._ask_line(f"  {message} ")
