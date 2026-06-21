"""Interactive prompts that degrade cleanly to non-interactive mode.

In non-interactive mode a question with a usable default returns it; one without raises
AbortByUser so an unattended run fails loudly instead of hanging on stdin.
"""
import sys

from .errors import AbortByUser


class Prompter:
    def __init__(self, log, *, interactive: bool, assume_yes: bool = False):
        self.log = log
        self.interactive = interactive
        self.assume_yes = assume_yes

    def _ask_line(self, text: str) -> str:
        sys.stdout.write(text)
        sys.stdout.flush()
        return sys.stdin.readline().rstrip("\n")

    def ask(self, question: str, *, default: str = None, key: str = None) -> str:
        if not self.interactive:
            if default is not None:
                self.log.detail(f"{question} -> {default} (non-interactive default)")
                return default
            raise AbortByUser(
                f"Need a value for: {question}",
                remediation=f"Provide it via --{(key or 'flag')} or the answer file, "
                            "or run interactively.")
        suffix = f" [{default}]" if default is not None else ""
        while True:
            ans = self._ask_line(f"  {question}{suffix}: ").strip()
            if ans:
                return ans
            if default is not None:
                return default

    def confirm(self, question: str, *, default: bool = False) -> bool:
        if self.assume_yes:
            self.log.detail(f"{question} -> yes (--assume-yes)")
            return True
        if not self.interactive:
            self.log.detail(f"{question} -> {default} (non-interactive default)")
            return default
        hint = "Y/n" if default else "y/N"
        ans = self._ask_line(f"  {question} [{hint}]: ").strip().lower()
        if not ans:
            return default
        return ans in ("y", "yes")

    def choose(self, question: str, options: list, *, default_index: int = 0) -> str:
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
            ans = self._ask_line(f"  choose [1-{len(options)}, default {default_index + 1}]: ").strip()
            if not ans:
                return options[default_index][0]
            if ans.isdigit() and 1 <= int(ans) <= len(options):
                return options[int(ans) - 1][0]

    def pause(self, message: str):
        """Block until the operator acknowledges (used for 'save these keys')."""
        if not self.interactive:
            return
        self._ask_line(f"  {message} ")
