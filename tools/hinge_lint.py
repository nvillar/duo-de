#!/usr/bin/env python3
"""hinge_lint: list UI elements that overlap the Surface Duo hinge.

Dumps the visible window hierarchy with `uiautomator dump` (plus an optional screenshot) from a connected device and
reports every node whose bounds intersect the hinge rectangle. Containers that merely span both panels are ignored; a
node is reported when it is a leaf, or carries text / a content description, or is clickable (that is, something the
user sees or touches).

  hinge_lint.py                   # current screen, spanned or not, report on stdout
  hinge_lint.py --shot out.png    # also save a screenshot with the hinge band and the offenders outlined
  hinge_lint.py --label shade     # name the capture; with --out DIR writes DIR/shade.{xml,json,png}
  hinge_lint.py --pkg com.android.systemui --pkg com.android.launcher3   # only these packages
  hinge_lint.py --xml dump.xml --width 2784 --height 1800                # offline, no device
  hinge_lint.py --xml dump.xml --width 1800 --height 2784 --device duo   # offline, rotated, only accept Duo 1 sizes

Exit status: 0 = clean (or a single panel, which has no hinge in view), 1 = overlaps found, 2 = tool/device error,
3 = unsupported display geometry (a size that is not a known Surface Duo layout, e.g. a scaled or letterboxed display).

Geometry, same table as the framework's DuoPanelGeometry and the DisplayMask HingeMath (natural orientation is the two
portrait panels side by side; --width/--height are the display size in the current rotation):

  device  panel  hinge  height   spanned      gapless      single
  duo      1350     84    1800   2784 x 1800  2700 x 1800  1350 x 1800
  duo2     1344     66    1892   2754 x 1892  2688 x 1892  1344 x 1892

Spanned: the hinge band is x panel..panel+hinge (y when rotated 90/270; the hinge is centred, so 180/270 give the same
band). Gapless (persist.sys.phh.duo.disable_hinge=1): the panels touch, so the "hinge" is the zero-width seam at x = panel
and a node is reported when it crosses that seam. Single panel: no hinge in view, nothing to report.
"""
import argparse, json, os, re, subprocess, sys
import xml.etree.ElementTree as ET

# device: (panel width, hinge width, panel height) in natural orientation
SPECS = {
    "duo": (1350, 84, 1800),
    "duo2": (1344, 66, 1892),
}
SPANNED, GAPLESS, SINGLE, UNKNOWN = "spanned", "gapless", "single", "unknown"
EXIT_CLEAN, EXIT_OVERLAPS, EXIT_ERROR, EXIT_UNSUPPORTED = 0, 1, 2, 3
BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


class ToolError(Exception):
    pass


def adb(*args, binary=False):
    try:
        r = subprocess.run(["adb", *args], capture_output=True)
    except OSError as e:
        raise ToolError(f"adb: {e}")
    if r.returncode:
        raise ToolError(f"adb {' '.join(args)}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout if binary else r.stdout.decode(errors="replace")


def natural_display_size():
    """`wm size` reports the display size in its natural (rotation 0) orientation, whatever the current rotation."""
    out = adb("shell", "wm", "size")
    m = re.search(r"Physical size: (\d+)x(\d+)", out)
    o = re.search(r"Override size: (\d+)x(\d+)", out)
    if not (o or m):
        raise ToolError(f"cannot parse `wm size`: {out.strip()!r}")
    return tuple(map(int, (o or m).groups()))


def dump_rotation(xml_text):
    """The `rotation` attribute uiautomator writes on <hierarchy>, or None when absent."""
    root = ET.fromstring(xml_text)
    r = root.get("rotation")
    return int(r) & 3 if r is not None and r.lstrip("-").isdigit() else None


def classify(width, height, devices=None):
    """(device, mode) for a display of this size in any rotation; (None, UNKNOWN) when it is no known Duo layout."""
    long_side, short_side = max(width, height), min(width, height)
    for device in devices or SPECS:
        panel, hinge, h = SPECS[device]
        if short_side == panel and long_side == h:
            return device, SINGLE
        if short_side != h:
            continue
        if long_side == 2 * panel + hinge:
            return device, SPANNED
        if long_side == 2 * panel:
            return device, GAPLESS
    return None, UNKNOWN


def hinge_rect(width, height, devices=None):
    """(device, mode, band): the hinge band (l, t, r, b) in current-display pixels, None for a single panel or an
    unknown size. A gapless band has zero width (or height): the seam between the two panels."""
    device, mode = classify(width, height, devices)
    if mode in (SINGLE, UNKNOWN):
        return device, mode, None
    panel, hinge, _ = SPECS[device]
    gap = hinge if mode == SPANNED else 0
    if width > height:                                          # rotation 0/180: panels side by side
        return device, mode, (panel, 0, panel + gap, height)
    return device, mode, (0, panel, width, panel + gap)         # rotation 90/270: panels stacked


def parse(xml_text):
    root = ET.fromstring(xml_text)
    for node in root.iter("node"):
        m = BOUNDS.fullmatch(node.get("bounds", ""))
        if not m:
            continue
        l, t, r, b = map(int, m.groups())
        yield node, (l, t, r, b)


def _overlaps(a0, a1, b0, b1):
    if b0 == b1:                     # a zero-width seam: overlapped only when strictly crossed
        return a0 < b0 < a1
    return a0 < b1 and a1 > b0


def intersects(a, h):
    return _overlaps(a[0], a[2], h[0], h[2]) and _overlaps(a[1], a[3], h[1], h[3])


def lint(xml_text, hinge, pkgs):
    hits = []
    for node, box in parse(xml_text):
        pkg = node.get("package", "")
        if pkgs and pkg not in pkgs:
            continue
        if box[2] <= box[0] or box[3] <= box[1] or not intersects(box, hinge):
            continue
        text = node.get("text") or node.get("content-desc")
        horizontal = hinge[3] - hinge[1] > hinge[2] - hinge[0]
        spans = (box[0] <= hinge[0] and box[2] >= hinge[2]) if horizontal else (box[1] <= hinge[1] and box[3] >= hinge[3])
        if spans and not text:      # a backdrop, scrim or container laid across both panels: fine
            continue
        leaf = len(node) == 0
        visible = node.get("text") or node.get("content-desc") or node.get("clickable") == "true"
        if not (leaf or visible):
            continue
        hits.append({
            "package": pkg, "class": node.get("class", ""), "id": node.get("resource-id", ""),
            "text": (node.get("text") or node.get("content-desc") or "")[:60],
            "bounds": list(box), "clickable": node.get("clickable") == "true",
            "inside_hinge": box[0] >= hinge[0] and box[2] <= hinge[2] and box[1] >= hinge[1] and box[3] <= hinge[3],
        })
    return hits


def annotate(png, hinge, hits, out):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        open(out, "wb").write(png)
        print("(Pillow not installed: saved the plain screenshot)", file=sys.stderr)
        return
    import io
    im = Image.open(io.BytesIO(png)).convert("RGB")
    d = ImageDraw.Draw(im, "RGBA")
    d.rectangle(hinge, fill=(255, 0, 0, 60), outline=(255, 0, 0, 160))
    for h in hits:
        d.rectangle(h["bounds"], outline=(255, 160, 0, 255), width=4)
    im.save(out)


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pkg", action="append", help="only report nodes of this package (repeatable)")
    ap.add_argument("--xml", help="lint an existing uiautomator dump instead of the device")
    ap.add_argument("--width", type=int, help="with --xml: display width in the dump's (current) rotation")
    ap.add_argument("--height", type=int, help="with --xml: display height in the dump's (current) rotation")
    ap.add_argument("--device", choices=sorted(SPECS), action="append",
                    help="only accept this device's geometry (repeatable; default: any known Duo)")
    ap.add_argument("--shot", help="save an annotated screenshot here")
    ap.add_argument("--out", help="directory for dump, json and screenshot (with --label)")
    ap.add_argument("--label", default="capture")
    ap.add_argument("--json", action="store_true", help="machine readable output")
    a = ap.parse_args(argv)

    if a.xml:
        if not (a.width and a.height):
            ap.error("--xml needs --width and --height")
        with open(a.xml, encoding="utf-8") as f:
            xml_text = f.read()
        w, h = a.width, a.height
    else:
        nw, nh = natural_display_size()
        adb("shell", "uiautomator", "dump", "/sdcard/hinge_lint.xml")
        xml_text = adb("exec-out", "cat", "/sdcard/hinge_lint.xml")
        rotation = dump_rotation(xml_text)
        if rotation is None:
            print("(no rotation in the uiautomator dump: assuming the natural orientation)", file=sys.stderr)
            rotation = 0
        w, h = (nh, nw) if rotation & 1 else (nw, nh)

    device, mode, hinge = hinge_rect(w, h, a.device)
    report = {"display": [w, h], "device": device, "mode": mode, "hinge": hinge, "hits": []}
    if mode == UNKNOWN:
        known = ", ".join(sorted(a.device or SPECS))
        msg = f"display {w}x{h}: unsupported geometry, not a known Surface Duo layout ({known}); nothing was linted"
        if a.json:
            print(json.dumps(dict(report, error=msg), indent=1))
        print(msg, file=sys.stderr)
        return EXIT_UNSUPPORTED
    if hinge is None:
        if a.json:
            print(json.dumps(report, indent=1))
        else:
            print(f"display {w}x{h} ({device} {mode}): single panel, there is no hinge in view")
        return EXIT_CLEAN
    hits = lint(xml_text, hinge, set(a.pkg or []))
    report["hits"] = hits

    shot = a.shot
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, a.label + ".xml"), "w", encoding="utf-8") as f:
            f.write(xml_text)
        with open(os.path.join(a.out, a.label + ".json"), "w") as f:
            json.dump(report, f, indent=1)
        shot = shot or os.path.join(a.out, a.label + ".png")
    if shot and not a.xml:
        annotate(adb("exec-out", "screencap", "-p", binary=True), hinge, hits, shot)

    if a.json:
        print(json.dumps(report, indent=1))
    else:
        what = "hinge band" if mode == SPANNED else "gapless seam"
        print(f"display {w}x{h} ({device} {mode}), {what} {hinge}: {len(hits)} overlap(s)")
        for h_ in hits:
            tag = "IN-HINGE" if h_["inside_hinge"] else "CROSSES "
            print(f"  {tag} {h_['package']} {h_['class'].split('.')[-1]:<22} {h_['bounds']} "
                  f"{'clickable ' if h_['clickable'] else ''}{h_['id'].split('/')[-1]} {h_['text']!r}")
    return EXIT_OVERLAPS if hits else EXIT_CLEAN


def main(argv=None):
    try:
        return run(argv)
    except ToolError as e:
        print(e, file=sys.stderr)
        return EXIT_ERROR
    except (OSError, ET.ParseError) as e:
        print(f"hinge_lint: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())