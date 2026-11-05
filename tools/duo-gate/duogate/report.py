"""results.json, report.md and the console summary."""
from __future__ import annotations

import json
import os

from .model import FAIL, INCOMPLETE, PASS, worst


def gate_verdicts(checks: list[dict], selected: list[str], aborted: dict) -> dict:
    out = {}
    for g in selected:
        cs = [c for c in checks if c["gate"] == g]
        if g in aborted and not cs:
            out[g] = {"status": INCOMPLETE, "note": "not run: " + aborted[g], "automated": 0, "manual": 0,
                      "manual_pending": 0}
            continue
        auto = [c for c in cs if not c["manual"]]
        manual = [c for c in cs if c["manual"]]
        status = worst(*[c["status"] for c in auto]) if auto else INCOMPLETE
        if any(c["status"] == FAIL for c in manual):
            status = FAIL
        note = aborted.get(g, "")
        if g in aborted:
            status = worst(status, INCOMPLETE)
        out[g] = {"status": status, "note": note, "automated": len(auto), "manual": len(manual),
                  "manual_pending": sum(1 for c in manual if c["status"] == INCOMPLETE)}
    return out


def overall(gates: dict, preflight: dict, restore: dict | None) -> str:
    s = worst(*[g["status"] for g in gates.values()]) if gates else INCOMPLETE
    s = worst(s, preflight.get("status", INCOMPLETE))
    if restore is not None and restore.get("status") != PASS:
        s = worst(s, INCOMPLETE)
    return s


def write(outdir: str, results: dict) -> tuple[str, str]:
    jpath = os.path.join(outdir, "results.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True)
    mpath = os.path.join(outdir, "report.md")
    with open(mpath, "w", encoding="utf-8") as f:
        f.write(render(results))
    return jpath, mpath


def _reasons(check: dict, limit: int = 6) -> list[str]:
    seen, out = set(), []
    for a in check["attempts"]:
        for r in a["reasons"]:
            if r not in seen:
                seen.add(r)
                out.append("#%d %s" % (a["index"], r))
    return out[:limit] + (["... %d more" % (len(out) - limit)] if len(out) > limit else [])


def render(r: dict) -> str:
    pre = r.get("preconditions", {})
    build = pre.get("build", {})
    lines = ["# duo-gate report %s" % r["run_id"], "",
             "Overall: **%s** (automated checks; manual checks never PASS)" % r["overall"], "",
             "Manual checks pending review: **%d** (operator-performed or operator-observed; evidence linked below)"
             % sum(g.get("manual_pending", 0) for g in r.get("gates", {}).values()), "",
             "| | |", "|---|---|",
             "| Build | %s (%s) |" % (build.get("ro.build.display.id"), build.get("ro.build.version.incremental")),
             "| Panels | %s |" % pre.get("panels"),
             "| HOME (recorded) | %s |" % pre.get("home"),
             "| Probe | %s |" % (pre.get("components", {}).get("probe"),),
             "| IME | %s |" % pre.get("ime"),
             "| Navigation mode | %s |" % pre.get("navigation_mode"),
             "| Size / density | %s / %s |" % (pre.get("size"), pre.get("density")),
             "| Secure lock | %s |" % pre.get("secure_lock"),
             "| Test settings applied | %s |" % (pre.get("test_settings") or "none"),
             "| Repetitions | %s |" % r.get("reps"),
             "| Started / finished | %s / %s |" % (r.get("started"), r.get("finished")), ""]
    pf = r.get("preflight", {})
    lines += ["Preflight: **%s** %s" % (pf.get("status"), "; ".join(pf.get("reasons", []))), ""]
    rs = r.get("restore")
    if rs is not None:
        lines += ["Restore: **%s** %s" % (rs.get("status"), "; ".join(rs.get("reasons", []))), ""]
    lines += ["## Gates", "", "| Gate | Verdict | Automated checks | Manual (pending review) | Note |",
              "|---|---|---|---|---|"]
    for g, gv in r["gates"].items():
        lines.append("| %s | **%s** | %d | %d (%d) | %s |" % (g, gv["status"], gv["automated"], gv["manual"],
                                                          gv["manual_pending"], gv.get("note", "")))
    lines += ["", "## Checks", "", "| Check | Status | Pass/Fail/Inc of required | Manual | Summary |",
              "|---|---|---|---|---|"]
    for c in r["checks"]:
        lines.append("| %s | **%s** | %d/%d/%d of %d | %s | %s |" % (
            c["id"], c["status"], c["pass"], c["fail"], c["incomplete"], c["required"], "yes" if c["manual"] else "",
            c["summary"]))
    lines += ["", "## Details", ""]
    for c in r["checks"]:
        rs_ = _reasons(c)
        notes = sorted({n for a in c["attempts"] for n in a["notes"]})[:6]
        if not rs_ and not notes and c["status"] == PASS:
            continue
        lines.append("### %s: %s" % (c["id"], c["title"]))
        for x in rs_:
            lines.append("- %s" % x)
        for n in notes:
            lines.append("- note: %s" % n)
        ev = [e for a in c["attempts"] for e in a["evidence"]][:4]
        if ev:
            lines.append("- evidence: %s" % ", ".join(ev))
        lines.append("")
    if r.get("errors"):
        lines += ["## Errors", ""] + ["- %s" % e for e in r["errors"]] + [""]
    lines += ["Evidence directory: `%s` (logcat.txt, snaps/, screens/, commands.log, operator.log)." % r.get("outdir"),
              ""]
    return "\n".join(lines)


def console(r: dict) -> str:
    out = ["", "duo-gate %s: %s" % (r["run_id"], r["overall"])]
    for g, gv in r["gates"].items():
        out.append("  %-4s %-10s auto=%d manual=%d pending=%d %s" % (g, gv["status"], gv["automated"], gv["manual"],
                                                                     gv["manual_pending"], gv.get("note", "")))
    for c in r["checks"]:
        out.append("    %-24s %-10s %d/%d/%d of %d %s" % (c["id"], c["status"], c["pass"], c["fail"],
                                                         c["incomplete"], c["required"], c["summary"]))
    out.append("  report: %s" % os.path.join(r.get("outdir", ""), "report.md"))
    return "\n".join(out)
