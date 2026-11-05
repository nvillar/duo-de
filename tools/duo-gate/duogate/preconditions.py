"""Preconditions (recorded before anything changes), test-setting overrides, and restoration.

restore.json is written before the first change, so `duo_gate.py restore <evidence dir>` can put the device back even
if a run was interrupted.
"""
from __future__ import annotations

import json
import os
import re
import time

from . import parsers
from .adb import AdbError
from .checks import DUO1, PROBE_PACKAGE, check_geometry
from .model import EvidenceError, Verdict

PROBE_HOME = PROBE_PACKAGE + "/.ProbeHomeActivity"
PROBE_PROTOCOL = "gate-2"
PANEL_PROPS = ("persist.sys.phh.duo.panels", "persist.debug.phh.duo.panels")
REBOOT_SETTLE = 5.0

# (namespace, key, value the gate needs, why). show_ime_with_hard_keyboard is deliberately not here: it is recorded in
# the preconditions and left at the device's value, because forcing it could mask IME defects.
TEST_SETTINGS = [
    ("global", "stay_on_while_plugged_in", "7", "keep the screen on during the run"),
    ("system", "accelerometer_rotation", "0", "deterministic rotation 0"),
    ("system", "user_rotation", "0", "deterministic rotation 0"),
]


def _try(fn, default=None):
    try:
        return fn()
    except (EvidenceError, AdbError) as e:
        return default if default is not None else "unavailable: %s" % e


def collect(dev, op) -> tuple[dict, Verdict]:
    """Records the preconditions of plan section 16.4 and checks those the gates depend on."""
    adb = dev.adb
    v = Verdict()
    props = dev.props()
    pre: dict = {"host_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "device_time": adb.shell("date '+%Y-%m-%d %H:%M:%S'").strip(),
                 "serial": adb.run(["get-serialno"]).strip()}
    pre["build"] = {k: props.get(k) for k in (
        "ro.build.display.id", "ro.build.version.incremental", "ro.build.type", "ro.build.date",
        "ro.product.device", "ro.system.build.fingerprint")}
    pre["build"].update({k: v_ for k, v_ in props.items() if k.startswith("ro.duode.")})

    comps = {}
    probe = _try(lambda: parsers.parse_package(adb.shell("dumpsys package " + PROBE_PACKAGE)), {})
    comps["probe"] = probe
    if isinstance(probe, dict) and probe.get("codePath"):
        comps["probe_apk_sha256"] = adb.shell("sha256sum %s/*.apk" % probe["codePath"], check=False).strip()
    comps["launcher3"] = adb.shell("pm path com.android.launcher3", check=False).strip()
    for jar in ("/system/framework/services.jar", "/system/framework/framework.jar"):
        comps[os.path.basename(jar) + "_sha256"] = adb.shell("sha256sum " + jar, check=False).strip()
    comps["systemui"] = adb.shell("pm path com.android.systemui", check=False).strip()
    pre["components"] = comps

    containers = _try(lambda: parsers.parse_containers(adb.shell("dumpsys activity containers")), {})
    pre["panels"] = {k: props.get(k, "") for k in PANEL_PROPS + ("sys.duode.panels.active",)}
    pre["panels"]["areas"] = containers.panel_names() if hasattr(containers, "panel_names") else str(containers)
    pre["home"] = _try(dev.home_component)
    pre["recents"] = _try(lambda: parsers.parse_recents_component(adb.shell("dumpsys activity recents")))
    pre["ime"] = {k: adb.setting("secure", k) for k in (
        "default_input_method", "enabled_input_methods", "show_ime_with_hard_keyboard")}
    pre["navigation_mode"] = adb.setting("secure", "navigation_mode")
    pre["density"] = _try(lambda: parsers.parse_wm_density(adb.shell("wm density")))
    pre["size"] = _try(lambda: parsers.parse_wm_size(adb.shell("wm size")))
    pre["display_offset"] = _try(lambda: parsers.parse_display_offset(adb.shell("dumpsys display")))
    pre["device_state"] = _try(lambda: parsers.parse_device_state(adb.shell("dumpsys device_state")))
    pre["rotation"] = {k: adb.setting("system", k) for k in ("accelerometer_rotation", "user_rotation")}
    pre["secure_lock"] = _try(lambda: parsers.parse_credential_type(adb.shell("dumpsys lock_settings")))
    pre["settings_before"] = {"%s/%s" % (ns, k): adb.setting(ns, k) for ns, k, _, _ in TEST_SETTINGS}
    pre["probe_targets_known"] = dev.log.latest(
        lambda e: e.event == "targets" and e.act_class == "ProbeHomeActivity") is not None

    # what the gates depend on
    if not isinstance(probe, dict) or not probe:
        v.incomplete("DuoProbe (%s) not installed" % PROBE_PACKAGE)
    elif probe.get("versionName") != PROBE_PROTOCOL:
        v.incomplete("DuoProbe versionName=%s, need %s (rebuild/install tools/duo-probe)"
                     % (probe.get("versionName"), PROBE_PROTOCOL))
    if pre["density"] is not None and not isinstance(pre["density"], str):
        phys, override = pre["density"]
        if (override or phys) != 400:
            v.incomplete("density %s, expected 400" % (override or phys))
    if isinstance(pre["size"], tuple):
        phys, override = pre["size"]
        if phys != (2784, 1800):
            v.incomplete("physical size %s, expected 2784x1800 (not a Duo 1?)" % (phys,))
        if override is not None:
            v.incomplete("override size %s: device is in a single-screen posture; unfold to dual" % (override,))
    else:
        v.incomplete("wm size: %s" % pre["size"])
    return pre, v


KEY_WAKEUP = 224


def _keyguard_showing(dev) -> bool | None:
    return parsers.parse_activities(dev.adb.shell("dumpsys activity activities")).keyguard_showing


def check_keyguard(dev, pre: dict, dismiss: bool, wait: float = 5.0) -> Verdict:
    """The keyguard must be gone before the probe home can be driven. Checked before anything else touches the UI so
    the reason is specific. With no secure lock and `dismiss`, it is dismissed (wake + wm dismiss-keyguard) and the
    action is recorded in pre["keyguard"]; nothing is restored for it. With a secure lock (or an unknown credential)
    it is never dismissed: INCOMPLETE with an operator instruction."""
    v = Verdict()
    state: dict = {}
    pre["keyguard"] = state
    try:
        showing = _keyguard_showing(dev)
    except (EvidenceError, AdbError) as e:
        state["showing_at_start"] = None
        return v.incomplete("keyguard state unknown: %s" % e)
    state["showing_at_start"] = showing
    if showing is None:
        return v.incomplete("keyguard state not found in dumpsys activity activities")
    if not showing:
        return v
    cred = pre.get("secure_lock")
    if cred != "NONE":
        return v.incomplete("keyguard showing (screen lock %s): unlock the device on screen, then run again" % cred)
    if not dismiss:
        return v.incomplete("keyguard showing (no secure lock): `run` dismisses it; `preflight` changes nothing")
    dev.adb.shell("input keyevent %d" % KEY_WAKEUP)
    dev.adb.shell("wm dismiss-keyguard", check=False)
    state["action"] = "wake + wm dismiss-keyguard (no secure lock); not restored"
    deadline = time.monotonic() + wait
    while True:
        try:
            showing = _keyguard_showing(dev)
        except (EvidenceError, AdbError) as e:
            state["dismissed"] = None
            return v.incomplete("keyguard state unknown after dismissing it: %s" % e)
        if showing is False or time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    state["dismissed"] = showing is False
    if showing is not False:
        v.incomplete("keyguard still showing after wake + wm dismiss-keyguard: unlock the device on screen")
    else:
        v.note("keyguard dismissed (no secure lock)")
    return v


def check_panel_mode(dev) -> Verdict:
    """Panel mode effective this boot, with the Duo 1 geometry."""
    v = Verdict()
    if dev.adb.getprop("sys.duode.panels.active") != "1":
        v.incomplete("sys.duode.panels.active != 1: panel mode is not active this boot")
    snap = dev.snap("preflight-geometry", kinds=("containers",))
    v.merge(check_geometry(snap, DUO1))
    return v


def write_restore(dev, pre: dict) -> str:
    data = {"home": pre.get("home"), "settings": pre.get("settings_before", {}),
            "panels": {k: pre["panels"].get(k, "") for k in PANEL_PROPS},
            "active": pre["panels"].get("sys.duode.panels.active")}
    return dev.write_json("restore.json", data)


def apply_test_settings(dev, pre: dict) -> list[str]:
    applied = []
    for ns, key, value, why in TEST_SETTINGS:
        if pre["settings_before"].get("%s/%s" % (ns, key)) != value:
            dev.adb.put_setting(ns, key, value)
            applied.append("%s/%s=%s (%s)" % (ns, key, value, why))
    if pre.get("home") != PROBE_HOME:
        dev.set_home(PROBE_HOME)
        applied.append("HOME=%s" % PROBE_HOME)
    return applied


def restore(dev, data: dict, reboot: bool = False) -> Verdict:
    """Puts back the recorded settings, HOME and panel properties, then reads them back. The panel properties are only
    read at boot: if they (or the boot's sys.duode.panels.active) differ from the recorded state, the device is
    rebooted only when `reboot` authorizes it; otherwise the result stays INCOMPLETE with the recovery command."""
    v = Verdict()
    adb = dev.adb
    for nk, value in data.get("settings", {}).items():
        ns, key = nk.split("/", 1)
        try:
            if adb.setting(ns, key) == value:
                continue            # never changed (e.g. preflight stopped before the test settings)
            adb.put_setting(ns, key, value)
            if adb.setting(ns, key) != value:
                v.fail("setting %s not restored (want %r)" % (nk, value))
        except AdbError as e:
            v.incomplete("setting %s: %s" % (nk, e))
    home = data.get("home")
    if isinstance(home, str) and re.fullmatch(r"[\w.]+/[\w.$]+", home) and "ResolverActivity" not in home:
        try:
            dev.set_home(home)
        except (EvidenceError, AdbError) as e:
            v.fail("HOME not restored to %s: %s" % (home, e))
    else:
        v.incomplete("original HOME unknown (%r); set it by hand" % (home,))

    reboot_needed = False
    for k, want in data.get("panels", {}).items():
        got = _try(lambda: adb.getprop(k))
        if parsers.same_bool_prop(got, want):
            continue
        adb.shell("setprop %s %s" % (k, want if want else "0"), check=False)
        got = _try(lambda: adb.getprop(k))
        if parsers.same_bool_prop(got, want):
            reboot_needed = True
            v.note("%s restored to %r" % (k, want))
        else:
            v.fail("%s=%r, could not restore the recorded %r (persist.sys.* needs adb root)" % (k, got, want))
    active, source = expected_active(data)
    if active is None:
        v.incomplete("recorded panel mode unknown (%s): the effective mode cannot be verified" % source)
    else:
        if source != "recorded":
            v.note("expected panel mode %r %s" % (active, source))
        now = _try(lambda: adb.getprop("sys.duode.panels.active"))
        if not parsers.same_bool_prop(now, active):
            reboot_needed = True
    if reboot_needed and active is None:
        v.incomplete("not rebooting: the expected panel mode is unknown, so a reboot could not be verified")
    elif reboot_needed:
        if not reboot:
            v.incomplete("panel mode differs from the recorded boot state; a reboot applies the restored properties: "
                         "duo_gate.py restore <evidence dir> --reboot (or adb reboot)")
        else:
            try:
                adb.run(["reboot"], check=False)
                time.sleep(REBOOT_SETTLE)
                adb.wait_for_boot()
                now = adb.getprop("sys.duode.panels.active")
                if not parsers.same_bool_prop(now, active):
                    v.fail("after reboot sys.duode.panels.active=%r, expected %r" % (now, active))
                else:
                    v.note("rebooted to apply the restored panel properties")
            except AdbError as e:
                v.incomplete("reboot to apply the panel properties: %s" % e)
    return v


def expected_active(data: dict) -> tuple[str | None, str]:
    """The effective panel mode (sys.duode.panels.active) the device must end in. Records written before the field
    existed fall back to preconditions.json (merged by load_restore), then to the recorded panel properties, so a
    pending reboot is never forgotten between two restore invocations."""
    if isinstance(data.get("active"), str):
        return data["active"], data.get("active_source", "recorded")
    panels = data.get("panels") or {}
    if panels and all(isinstance(x, str) for x in panels.values()):
        # the framework enables panel mode when either flag reads true with SystemProperties.getBoolean
        flags = [parsers.android_bool(x) for x in panels.values()]
        if any(f is True for f in flags):
            return "1", "derived from the recorded panel properties %s" % panels
        if all(f is False for f in flags):
            return "0", "derived from the recorded panel properties %s" % panels
        return None, "unparsable recorded panel properties %s" % panels
    return None, "unknown"


def load_restore(outdir: str) -> dict:
    with open(os.path.join(outdir, "restore.json"), encoding="utf-8") as f:
        data = json.load(f)
    if "active" not in data:
        try:
            with open(os.path.join(outdir, "preconditions.json"), encoding="utf-8") as f:
                active = json.load(f).get("panels", {}).get("sys.duode.panels.active")
            if isinstance(active, str):
                data["active"], data["active_source"] = active, "from preconditions.json (legacy record)"
        except (OSError, ValueError):
            pass
    return data
