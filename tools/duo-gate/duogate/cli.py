"""Command line: preflight, run, restore, report, list."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import traceback

from . import VERSION, gates, preconditions, report
from .adb import Adb, AdbError
from .device import KEY_HOME, Device
from .model import EvidenceError, INCOMPLETE, PASS, Verdict
from .prompts import Operator


def _verdict_json(v: Verdict) -> dict:
    return {"status": v.status, "reasons": v.reasons, "notes": v.notes}


def _open_run(args) -> tuple[str, str, Adb]:
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    outdir = os.path.abspath(os.path.join(args.out, "duogate-" + run_id))
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "commands.log")

    def log(line):
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), line))
    return run_id, outdir, Adb(args.serial, args.adb, log)


def cmd_run(args, preflight_only: bool = False) -> int:
    selected = gates.DEFAULT if not args.gates else (gates.ORDER if args.gates == ["all"] else args.gates)
    unknown = [g for g in selected if g not in gates.GATES]
    if unknown:
        print("unknown gate(s): %s (known: %s)" % (unknown, ", ".join(gates.ORDER)), file=sys.stderr)
        return 3
    selected = [g for g in gates.ORDER if g in selected]
    run_id, outdir, adb = _open_run(args)
    print("duo-gate %s, evidence: %s" % (VERSION, outdir))
    results = {"run_id": run_id, "tool": VERSION, "outdir": outdir, "reps": args.reps, "selected": selected,
               "started": time.strftime("%Y-%m-%d %H:%M:%S"), "errors": []}
    op = Operator(not args.no_input, os.path.join(outdir, "operator.log"))
    dev = None
    ctx = None
    aborted: dict = {}
    restore_v = None
    pre: dict = {}
    pf = Verdict()
    preflight_done = False
    try:
        try:
            dev = Device(adb, outdir, run_id, settle=args.settle, screens=args.screens)
            pre, pf = preconditions.collect(dev, op)
            preconditions.write_restore(dev, pre)
            posture = op.yes_no("Is the device unfolded with BOTH panels lit, hinge vertical, rotation 0 (left = A)?")
            pre["operator_posture_dual"] = posture
            pre["posture_source"] = "operator prompt"
            if posture is None and args.posture_confirmed:
                posture = pre["operator_posture_dual"] = True
                pre["posture_source"] = "--posture-confirmed (operator assertion on the command line)"
            if posture is False:
                pf.incomplete("operator: device not in the dual posture")
            elif posture is None:
                pf.incomplete("dual posture not confirmed (no operator answer): answer the prompt, or pass "
                              "--posture-confirmed when running without a terminal")
            # keyguard first: otherwise it only shows up later as "no probe home buttons"
            pf.merge(preconditions.check_keyguard(dev, pre, dismiss=not preflight_only and pf.ok))
            if pf.ok:
                if not preflight_only:
                    pre["test_settings"] = preconditions.apply_test_settings(dev, pre)
                pf.merge(preconditions.check_panel_mode(dev))
            dev.write_json("preconditions.json", pre)
            if pf.ok and not preflight_only:
                dev.remove_probe_tasks()
                dev.key(KEY_HOME)
                time.sleep(1)
                dev.home_targets()      # fail early if the probe home cannot be driven
            preflight_done = True
        except (EvidenceError, AdbError) as e:
            pf.incomplete("preflight: %s" % e)
        except KeyboardInterrupt:
            pf.incomplete("preflight interrupted by the operator")
        except Exception as e:      # noqa: BLE001 - a tool error leaves the preflight INCOMPLETE, never PASS
            pf.incomplete("preflight tool error: %s: %s" % (type(e).__name__, e))
            results["errors"].append("preflight: %s" % traceback.format_exc())
        results["preflight"] = _verdict_json(pf)
        if dev is not None and pre:
            dev.write_json("preconditions.json", pre)

        if pf.ok and not preflight_only:
            ctx = gates.Ctx(dev, op, args.reps, pre, {"allow_reboot": args.allow_reboot,
                                                      "g6_unlock": args.g6_unlock, "launcher_app": args.launcher_app,
                                                      "quickstep": args.quickstep})
            for g in selected:
                print("== %s" % g, flush=True)
                try:
                    gates.GATES[g](ctx)
                except (AdbError, KeyboardInterrupt) as e:
                    why = "device lost: %s" % e if isinstance(e, AdbError) else "interrupted by the operator"
                    aborted[g] = why
                    for rest in selected[selected.index(g) + 1:]:
                        aborted[rest] = "not run (%s)" % why
                    break
                except Exception as e:      # noqa: BLE001 - any tool error leaves the gate INCOMPLETE, never PASS
                    aborted[g] = "tool error: %s" % e
                    results["errors"].append("%s: %s" % (g, traceback.format_exc()))
                    try:
                        dev.remove_probe_tasks()
                    except (EvidenceError, AdbError):
                        pass
        elif not preflight_only:
            for g in selected:
                aborted[g] = "preflight not clean"
    finally:
        # Always: restoration (independent of task cleanup), then the report, whatever happened above.
        if not preflight_only:
            restore_v = _restore_after_run(dev, outdir, args)
        if dev is not None:
            dev.close()
        op.close()
        if not preflight_done and pf.ok:
            # anything that escaped the preflight (e.g. SystemExit) must never leave it reported as PASS
            pf.incomplete("preflight did not finish")
        results["preflight"] = _verdict_json(pf)
        checks = [c.to_json() for c in (ctx.checks if ctx else [])]
        for g in selected:
            if not preflight_only and g not in aborted and not any(c["gate"] == g for c in checks):
                aborted[g] = "not run"
        results.update({"finished": time.strftime("%Y-%m-%d %H:%M:%S"), "preconditions": pre, "checks": checks,
                        "aborted": aborted, "restore": _verdict_json(restore_v) if restore_v else None})
        results["gates"] = report.gate_verdicts(checks, [] if preflight_only else selected, aborted)
        results["overall"] = (pf.status if preflight_only else
                              report.overall(results["gates"], results["preflight"], results["restore"]))
        report.write(outdir, results)
        print(report.console(results))
        if restore_v is not None and not restore_v.ok:
            print("WARNING: restore incomplete: %s" % "; ".join(restore_v.reasons), file=sys.stderr)
    return {PASS: 0, "FAIL": 1, INCOMPLETE: 2}[results["overall"]]


def _restore_after_run(dev, outdir: str, args) -> Verdict | None:
    """Best-effort probe task cleanup, then restoration of everything restore.json recorded; neither step can skip
    the other, and an interrupt during restoration is reported, not swallowed silently."""
    if dev is None or args.keep_settings or not os.path.exists(os.path.join(outdir, "restore.json")):
        return None
    notes = []
    try:
        dev.remove_probe_tasks()
    except (EvidenceError, AdbError, KeyboardInterrupt) as e:
        notes.append("probe task cleanup failed: %s" % (e or "interrupted"))
    try:
        v = preconditions.restore(dev, preconditions.load_restore(outdir), reboot=args.allow_reboot)
    except (EvidenceError, AdbError, OSError) as e:
        v = Verdict().incomplete("restore: %s" % e)
    except KeyboardInterrupt:
        v = Verdict().incomplete("restore interrupted: run `duo_gate.py restore %s`" % outdir)
    for n in notes:
        v.note(n)
    return v


def cmd_restore(args) -> int:
    adb = Adb(args.serial, args.adb)
    dev = Device(adb, args.evidence, "restore-" + secrets.token_hex(2), live=False)
    v = preconditions.restore(dev, preconditions.load_restore(args.evidence), reboot=args.reboot)
    print("restore: %s %s" % (v.status, "; ".join(v.reasons)))
    return 0 if v.ok else 2


def cmd_report(args) -> int:
    with open(os.path.join(args.evidence, "results.json"), encoding="utf-8") as f:
        results = json.load(f)
    report.write(args.evidence, results)
    print(report.console(results))
    return 0


def cmd_list(args) -> int:
    for g in gates.ORDER:
        doc = (gates.GATES[g].__doc__ or "").strip()
        print("%-4s %s%s" % (g, "(default) " if g in gates.DEFAULT else "", doc))
    return 0


def _positive_int(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("not an integer: %r" % text) from None
    if n < 1:
        raise argparse.ArgumentTypeError("must be >= 1 (a check with no repetition can never pass)")
    return n


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="duo_gate.py", description="Fail-closed gate collector for the Duo 1 two panel "
                                "prototype (plan section 16.4). See tools/duo-gate/README.md.")
    p.add_argument("--serial", help="adb device serial")
    p.add_argument("--adb", default=os.environ.get("ADB", "adb"), help="adb executable (default: adb or $ADB)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def run_opts(sp):
        sp.add_argument("--out", default=os.path.join(os.path.expanduser("~"), "duogate-evidence"),
                        help="parent directory for evidence (default: ~/duogate-evidence, outside the repository)")
        sp.add_argument("--no-input", action="store_true", help="no operator prompts: manual steps stay INCOMPLETE")
        sp.add_argument("--posture-confirmed", action="store_true",
                        help="operator assertion that the device is unfolded, both panels lit, rotation 0 (recorded); "
                             "used when the posture prompt gets no answer (--no-input or no terminal)")

    pre = sub.add_parser("preflight", help="record and check the preconditions only (changes nothing)")
    run_opts(pre)
    run = sub.add_parser("run", help="run gates (default: %s; 'all' for every gate)" % " ".join(gates.DEFAULT))
    run_opts(run)
    run.add_argument("gates", nargs="*", help="gates to run: %s, or all" % " ".join(gates.ORDER))
    run.add_argument("--reps", type=_positive_int, default=10, help="repetitions per check (>= 1, default 10)")
    run.add_argument("--settle", type=float, default=8.0, help="max seconds to wait for expected events (default 8)")
    run.add_argument("--screens", choices=("key", "all", "none"), default="key", help="screenshots (default: key)")
    run.add_argument("--allow-reboot", action="store_true", help="allow reboots: the panel-off control (PO), and a "
                     "reboot at restore time when the panel properties had to be restored")
    run.add_argument("--g6-unlock", choices=("pin", "manual"), default="pin",
                     help="G6 unlock: the tool types the PIN you enter (never logged), or you unlock on the device")
    run.add_argument("--launcher-app", default="com.android.calendar", help="app tapped in Launcher3 for G1L")
    run.add_argument("--quickstep", default="com.android.launcher3/.uioverrides.QuickstepLauncher",
                     help="Launcher3 HOME component for G1L/G3")
    run.add_argument("--keep-settings", action="store_true", help="do not restore HOME/settings at the end")
    rs = sub.add_parser("restore", help="restore HOME, settings and panel properties recorded in an evidence directory")
    rs.add_argument("evidence")
    rs.add_argument("--reboot", action="store_true", help="reboot if the restored panel properties need it")
    rp = sub.add_parser("report", help="re-render report.md from results.json")
    rp.add_argument("evidence")
    sub.add_parser("list", help="list the gates")
    args = p.parse_args(argv)
    if args.cmd == "preflight":
        args.gates, args.reps, args.settle, args.screens = [], 0, 8.0, "key"
        args.allow_reboot, args.g6_unlock, args.launcher_app, args.quickstep = False, "pin", None, None
        args.keep_settings = True
        return cmd_run(args, preflight_only=True)
    return {"run": cmd_run, "restore": cmd_restore, "report": cmd_report, "list": cmd_list}[args.cmd](args)
