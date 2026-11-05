"""adb transport. Every command is logged (secrets redacted); a lost device raises AdbError, which the runner turns into
INCOMPLETE for whatever was in progress."""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import time


class AdbError(Exception):
    """The device is gone, adb failed or a command timed out."""


_LOST = re.compile(r"no devices|device offline|device unauthorized|error: closed|protocol fault|"
                   r"device '[^']*' not found|more than one device")


class Adb:
    def __init__(self, serial: str | None = None, adb: str = "adb", log=None, timeout: float = 60.0):
        self.serial = serial
        self.adb = adb
        self.timeout = timeout
        self._log = log or (lambda s: None)

    def _base(self) -> list[str]:
        return [self.adb] + (["-s", self.serial] if self.serial else [])

    def run(self, args: list[str], timeout: float | None = None, secret: bool = False, check: bool = True,
            binary: bool = False):
        shown = " ".join(args) if not secret else args[0] + " <redacted>"
        t0 = time.monotonic()
        try:
            p = subprocess.run(self._base() + args, capture_output=True, timeout=timeout or self.timeout)
        except subprocess.TimeoutExpired:
            self._log("adb %s -> TIMEOUT" % shown)
            raise AdbError("adb %s timed out" % shown) from None
        except OSError as e:
            raise AdbError("cannot run %s: %s" % (self.adb, e)) from None
        out = p.stdout if binary else p.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        err = p.stderr.decode("utf-8", "replace").strip()
        self._log("adb %s -> rc=%d %.1fs%s" % (shown, p.returncode, time.monotonic() - t0,
                                               (" stderr=" + err[:200]) if err and not secret else ""))
        if p.returncode != 0 and check:
            if _LOST.search(err.lower()):
                raise AdbError("device lost during adb %s: %s" % (shown, err[:200]))
            raise AdbError("adb %s failed (rc=%d): %s" % (shown, p.returncode, "<redacted>" if secret else err[:300]))
        return out

    def shell(self, command: str, timeout: float | None = None, secret: bool = False, check: bool = True) -> str:
        return self.run(["shell", command], timeout=timeout, secret=secret, check=check)

    def getprop(self, name: str) -> str:
        return self.shell("getprop " + shlex.quote(name)).strip()

    def setting(self, namespace: str, key: str) -> str | None:
        v = self.shell("settings get %s %s" % (namespace, shlex.quote(key))).strip()
        return None if v == "null" else v

    def put_setting(self, namespace: str, key: str, value: str | None) -> None:
        if value is None:
            self.shell("settings delete %s %s" % (namespace, shlex.quote(key)))
        else:
            self.shell("settings put %s %s %s" % (namespace, shlex.quote(key), shlex.quote(value)))

    def pull(self, remote: str, local: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(local)), exist_ok=True)
        self.run(["pull", remote, local], timeout=120)

    def screenshot(self, local: str) -> None:
        """Captured on the device and pulled: exec-out redirection corrupts PNGs in Windows PowerShell 5."""
        remote = "/data/local/tmp/duogate-screen.png"
        self.shell("screencap -p " + remote)
        self.pull(remote, local)
        self.shell("rm -f " + remote, check=False)

    def wait_for_boot(self, timeout: float = 300.0) -> None:
        deadline = time.monotonic() + timeout
        self.run(["wait-for-device"], timeout=timeout)
        while time.monotonic() < deadline:
            try:
                if self.getprop("sys.boot_completed") == "1":
                    return
            except AdbError:
                pass
            time.sleep(3)
        raise AdbError("device did not finish booting within %ds" % timeout)

    def logcat_command(self, buffers: str = "main,system,crash") -> list[str]:
        # No -T: the existing buffer is replayed first, so layout (targets) events logged before the run are known.
        # Gate evidence is always bounded by markers, so replayed lines can never satisfy a check.
        return self._base() + ["logcat", "-v", "threadtime", "-b", buffers]
