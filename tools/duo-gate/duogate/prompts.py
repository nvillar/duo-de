"""Operator prompts for the manual parts of the gates. Answers are recorded (operator.log); secrets never are."""
from __future__ import annotations

import getpass
import sys
import time


class Operator:
    def __init__(self, interactive: bool = True, transcript: str | None = None, inp=input, secret_inp=None,
                 out=None):
        self.interactive = interactive
        self._inp = inp
        self._secret = secret_inp or getpass.getpass
        self._out = out or sys.stdout
        self._transcript = open(transcript, "a", encoding="utf-8") if transcript else None

    def _record(self, text: str) -> None:
        if self._transcript:
            self._transcript.write("%s %s\n" % (time.strftime("%H:%M:%S"), text))
            self._transcript.flush()

    def say(self, msg: str) -> None:
        print(msg, file=self._out, flush=True)
        self._record("SAY " + msg.replace("\n", " | "))

    def wait(self, msg: str) -> bool:
        """Shows an instruction and waits for Enter. False when not interactive or the operator typed 's' (skip)."""
        if not self.interactive:
            self._record("SKIPPED (non-interactive) " + msg)
            return False
        ans = self._inp("\n>>> %s\n    [Enter when done, s to skip] " % msg).strip().lower()
        self._record("WAIT %s -> %s" % (msg.replace("\n", " | "), ans or "done"))
        return ans != "s"

    def yes_no(self, msg: str) -> bool | None:
        """None when not interactive or skipped."""
        if not self.interactive:
            self._record("SKIPPED (non-interactive) " + msg)
            return None
        while True:
            ans = self._inp("\n??? %s [y/n/s] " % msg).strip().lower()
            if ans in ("y", "n", "s"):
                self._record("ASK %s -> %s" % (msg, ans))
                return None if ans == "s" else ans == "y"

    def choose(self, msg: str, choices: list[str]) -> str | None:
        if not self.interactive:
            self._record("SKIPPED (non-interactive) " + msg)
            return None
        while True:
            ans = self._inp("\n??? %s %s (s to skip) " % (msg, "/".join(choices))).strip().lower()
            if ans == "s":
                self._record("ASK %s -> skip" % msg)
                return None
            if ans in choices:
                self._record("ASK %s -> %s" % (msg, ans))
                return ans

    def secret(self, msg: str) -> str | None:
        if not self.interactive:
            return None
        value = self._secret("\n*** %s (not echoed, not logged): " % msg)
        self._record("SECRET %s -> <%s>" % (msg, "entered" if value else "empty"))
        return value or None

    def close(self) -> None:
        if self._transcript:
            self._transcript.close()
            self._transcript = None
