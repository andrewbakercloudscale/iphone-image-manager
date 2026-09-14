#!/usr/bin/env python3
"""
P0 transport spike for iPhone Image Manager.

Read-only. Enumerates a tethered iPhone through ImageCaptureCore via the
iimhelper Swift binary, then answers the nine questions in docs/PLAN.md
section 2 from the data rather than from assumption.

Nothing is downloaded, modified or deleted. Usage:

    python3 spikes/run_p0.py
    python3 spikes/run_p0.py --compare-presentations   # slower, runs twice
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HELPER_DIR = REPO / "spikes" / "iimhelper"
HELPER_BIN = HELPER_DIR / ".build" / "release" / "iimhelper"
OUT_DIR = REPO / "spikes" / "output"

# Apple device screen sizes in pixels, either orientation. A PNG whose
# dimensions match one of these and which carries no camera EXIF is a
# screenshot with high confidence.
SCREEN_SIZES = {
    (640, 1136), (750, 1334), (828, 1792), (1080, 1920), (1125, 2436),
    (1170, 2532), (1179, 2556), (1206, 2622), (1242, 2208), (1242, 2688),
    (1284, 2778), (1290, 2796), (1320, 2868), (1536, 2048), (1620, 2160),
    (1640, 2360), (1668, 2224), (1668, 2388), (2048, 2732),
}
SCREEN_SIZES |= {(h, w) for w, h in SCREEN_SIZES}

# Fields we want to know the real-world availability of. P0 question 2.
TRACKED_FIELDS = [
    "originatingAssetID", "fingerprint", "gpsString", "groupUUID", "burstUUID",
    "relatedUUID", "pairedRawImage", "sidecarFiles", "originalFilename",
    "createdFilename", "fileSystemPath", "exifCreationDate", "creationDate",
    "fileCreationDate", "width", "height", "duration", "uti", "folder",
]


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:,.1f} {unit}"
        n /= 1024.0
    return f"{n:,.1f} PB"


class Reporter:
    """Collects lines for both the terminal and the findings file."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, text: str = "") -> None:
        print(text)
        self.lines.append(text)

    def head(self, text: str) -> None:
        self("")
        self(f"## {text}")
        self("")


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------

def preflight(say: Reporter) -> dict:
    say.head("Preflight")
    facts: dict = {}
    fatal = []

    if sys.platform != "darwin":
        fatal.append("This spike only runs on macOS.")

    if not HELPER_BIN.exists():
        say("iimhelper not built, building it now")
        r = subprocess.run(["swift", "build", "-c", "release"], cwd=HELPER_DIR)
        if r.returncode != 0 or not HELPER_BIN.exists():
            fatal.append("Could not build iimhelper. Run: cd spikes/iimhelper && swift build -c release")
    if HELPER_BIN.exists():
        say(f"  helper            {HELPER_BIN.relative_to(REPO)}")

    # Photos and Image Capture take an exclusive session on the device.
    for app in ("Photos", "Image Capture", "ImageCaptureService"):
        r = subprocess.run(["pgrep", "-x", app], capture_output=True, text=True)
        if r.returncode == 0 and app in ("Photos", "Image Capture"):
            fatal.append(f"{app} is running and will hold an exclusive session on the iPhone. Quit it.")
    say("  conflicting apps  none running" if not fatal else "  conflicting apps  SEE BELOW")

    # Device identity via libimobiledevice, independent of ImageCaptureCore.
    if shutil.which("idevice_id"):
        r = subprocess.run(["idevice_id", "-l"], capture_output=True, text=True, timeout=20)
        udids = [u for u in r.stdout.split() if u]
        facts["udids"] = udids
        if udids:
            say(f"  libimobiledevice  {len(udids)} device(s): {', '.join(udids)}")
            info = {}
            r = subprocess.run(["ideviceinfo", "-u", udids[0]], capture_output=True, text=True, timeout=30)
            for line in r.stdout.splitlines():
                if ": " in line:
                    k, _, v = line.partition(": ")
                    info[k.strip()] = v.strip()
            for key in ("DeviceName", "ProductType", "ProductVersion", "UniqueDeviceID"):
                if key in info:
                    say(f"    {key:<18}{info[key]}")
            facts["ideviceinfo"] = info
        else:
            fatal.append("libimobiledevice sees no device. Plug the iPhone in, unlock it, and tap Trust.")
    else:
        say("  libimobiledevice  not installed, skipping the independent identity check")

    facts["macfuse"] = Path("/Library/Filesystems/macfuse.fs").exists() and bool(shutil.which("ifuse"))
    say(f"  ifuse cross-check {'available' if facts['macfuse'] else 'unavailable (macFUSE or ifuse missing)'}")

    if fatal:
        say("")
        for f in fatal:
            say(f"  BLOCKED: {f}")
        sys.exit(1)

    say("")
    say("  Keep the iPhone UNLOCKED for the whole run. A large library can take")
    say("  many minutes to build its content catalog.")
    return facts


# --------------------------------------------------------------------------
# Running the helper
# --------------------------------------------------------------------------

def run_helper(presentation: str, timeout: int, say: Reporter) -> tuple[list[dict], int, float]:
    say(f"  running helper with presentation={presentation} ...")
    started = time.monotonic()
    records: list[dict] = []
    proc = subprocess.Popen(
        [str(HELPER_BIN), "probe", "--presentation", presentation, "--timeout", str(timeout)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
    )
    assert proc.stdout is not None
    assets = 0
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            say(f"    unparseable helper output: {line[:120]}")
            continue
        records.append(rec)
        ev = rec.get("event")
        if ev == "asset":
            assets += 1
            if assets % 1000 == 0:
                print(f"    {assets:,} assets ...", end="\r", flush=True)
        elif ev == "catalogProgress":
            print(f"    building catalog {rec.get('percent')}% ...", end="\r", flush=True)
        elif ev in ("deviceFound", "sessionOpened", "catalogComplete", "mediaPresentationSet",
                    "mediaPresentationUnavailable", "accessRestrictionEnabled"):
            say(f"    {ev}: {json.dumps({k: v for k, v in rec.items() if k != 'event'})}")
        elif ev == "error":
            say(f"    ERROR {rec.get('code','')}: {rec.get('message','')}")
    proc.wait()
    elapsed = time.monotonic() - started
    stderr = (proc.stderr.read() if proc.stderr else "") or ""
    for line in stderr.splitlines():
        if "FATAL" in line:
            say(f"    {line}")
    return records, proc.returncode, elapsed


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

def analyse(records: list[dict], say: Reporter, facts: dict) -> dict:
    device = next((r for r in records if r.get("event") == "device"), None)
    summary = next((r for r in records if r.get("event") == "summary"), None)
    assets = [r for r in records if r.get("event") == "asset"]
    out: dict = {"assetCount": len(assets)}

    if device is None:
        say("  No device record. The probe did not reach a usable session.")
        return out

    # Q4, Q5, Q6: answered directly by device properties.
    say.head("Q4 / Q6: deletion capability and iCloud state")
    say(f"  canDeleteOneFile          {device.get('canDeleteOneFile')}")
    say(f"  canDeleteAllFiles         {device.get('canDeleteAllFiles')}")
    say(f"  iCloudPhotosEnabled       {device.get('iCloudPhotosEnabled')}")
    say(f"  isAccessRestricted        {device.get('isAccessRestrictedAppleDevice')}")
    say(f"  supportsHEIF              {device.get('supportsHEIF')}")
    say(f"  mediaPresentationInEffect {device.get('mediaPresentationInEffect')}")
    say(f"  capabilities              {device.get('capabilities')}")
    out["device"] = device

    if not device.get("canDeleteOneFile"):
        say("")
        say("  WARNING: the device does not advertise ICCameraDeviceCanDeleteOneFile.")
        say("  Removal through ImageCaptureCore would not be possible. This is the")
        say("  single most important finding in the spike.")

    if not assets:
        say("  No assets enumerated.")
        return out

    # Q1: totals
    say.head("Q1: what ImageCaptureCore sees")
    total = sum(a.get("fileSize") or 0 for a in assets)
    say(f"  assets                    {len(assets):,}")
    say(f"  total bytes               {human_bytes(total)}")
    if summary:
        say(f"  non-file items            {summary.get('nonFileItems')}")
        say(f"  catalog + dump elapsed    {summary.get('elapsedSeconds', 0):.1f}s")
    by_uti = Counter(a.get("uti") or "unknown" for a in assets)
    bytes_by_uti: dict[str, int] = defaultdict(int)
    for a in assets:
        bytes_by_uti[a.get("uti") or "unknown"] += a.get("fileSize") or 0
    say("")
    say(f"  {'UTI':<36}{'COUNT':>10}  {'BYTES':>12}")
    for uti, n in by_uti.most_common():
        say(f"  {uti:<36}{n:>10,}  {human_bytes(bytes_by_uti[uti]):>12}")
    out["byUTI"] = dict(by_uti)
    out["totalBytes"] = total

    # Q2: which metadata fields actually carry data
    say.head("Q2: metadata field availability across the real library")
    say("  This is the empirical answer to what the scanner can rely on.")
    say("")
    say(f"  {'FIELD':<26}{'PRESENT':>10}{'PERCENT':>10}   VERDICT")
    coverage = {}
    for field in TRACKED_FIELDS:
        present = 0
        for a in assets:
            v = a.get(field)
            if v is None:
                continue
            if isinstance(v, (list, str)) and len(v) == 0:
                continue
            if isinstance(v, (int, float)) and v == 0:
                continue
            present += 1
        pct = 100.0 * present / len(assets)
        coverage[field] = {"present": present, "percent": round(pct, 2)}
        verdict = "reliable" if pct >= 99 else "usable" if pct >= 50 else "sparse" if pct > 0 else "ABSENT"
        say(f"  {field:<26}{present:>10,}{pct:>9.1f}%   {verdict}")
    out["fieldCoverage"] = coverage

    say("")
    say("  Fields that do NOT exist in this API at all, confirming the plan's")
    say("  section 5 conflict 4: album membership, source application bundle id.")

    # Q3: asset grouping
    say.head("Q3: can compound assets be grouped")
    live = sum(1 for a in assets if a.get("relatedUUID"))
    bursts = Counter(a["burstUUID"] for a in assets if a.get("burstUUID"))
    groups = Counter(a["groupUUID"] for a in assets if a.get("groupUUID"))
    paired_raw = sum(1 for a in assets if a.get("pairedRawImage"))
    sidecars = sum(1 for a in assets if a.get("sidecarFiles"))
    say(f"  assets with relatedUUID   {live:,}   (Live Photo / edited relationships)")
    say(f"  distinct burstUUIDs       {len(bursts):,} covering {sum(bursts.values()):,} assets")
    say(f"  distinct groupUUIDs       {len(groups):,} covering {sum(groups.values()):,} assets")
    say(f"  assets with pairedRawImage {paired_raw:,}")
    say(f"  assets with sidecarFiles  {sidecars:,}")
    multi = sum(1 for c in groups.values() if c > 1)
    say(f"  groupUUIDs with >1 member {multi:,}")
    out["grouping"] = {"relatedUUID": live, "burstUUIDs": len(bursts),
                       "groupUUIDs": len(groups), "multiMemberGroups": multi,
                       "pairedRaw": paired_raw, "sidecars": sidecars}

    # Dedupe without downloading
    say.head("Bonus: can exact duplicates be found without downloading")
    fps = Counter(a["fingerprint"] for a in assets if a.get("fingerprint"))
    if fps:
        dupe_groups = {k: v for k, v in fps.items() if v > 1}
        wasted = 0
        by_fp: dict[str, list[dict]] = defaultdict(list)
        for a in assets:
            if a.get("fingerprint"):
                by_fp[a["fingerprint"]].append(a)
        for fp in dupe_groups:
            members = sorted(by_fp[fp], key=lambda x: x.get("fileSize") or 0, reverse=True)
            wasted += sum(m.get("fileSize") or 0 for m in members[1:])
        say(f"  assets with a fingerprint {sum(fps.values()):,}")
        say(f"  duplicate groups          {len(dupe_groups):,}")
        say(f"  redundant bytes           {human_bytes(wasted)}")
        say("")
        say("  If fingerprint is stable, exact dedupe needs no download at all,")
        say("  which changes the shape of the dedupe phase considerably.")
        out["fingerprintDupes"] = {"groups": len(dupe_groups), "wastedBytes": wasted}
    else:
        say("  No fingerprints exposed. Exact dedupe requires downloading and hashing.")
        out["fingerprintDupes"] = None

    # Q5: proxy detection
    say.head("Q5: iCloud proxy detection signal")
    suspects = []
    for a in assets:
        w, h = a.get("width") or 0, a.get("height") or 0
        size = a.get("fileSize") or 0
        uti = a.get("uti") or ""
        if not w or not h or not size:
            continue
        if uti.endswith("movie") or uti.endswith("mpeg-4") or (a.get("duration") or 0) > 0:
            continue
        pixels = w * h
        bpp = size / pixels if pixels else 0
        # A full-resolution capture is not this small per pixel.
        if pixels >= 1_000_000 and bpp < 0.12:
            suspects.append((a, bpp))
    say(f"  assets scored             {len(assets):,}")
    say(f"  low bytes-per-pixel       {len(suspects):,}")
    if suspects:
        suspects.sort(key=lambda t: t[1])
        say("")
        say(f"  {'NAME':<22}{'WxH':>14}{'SIZE':>11}{'B/PX':>8}")
        for a, bpp in suspects[:10]:
            name = str(a.get("name"))[:21]
            dims = "{}x{}".format(a.get("width"), a.get("height"))
            size = human_bytes(a.get("fileSize") or 0)
            say(f"  {name:<22}{dims:>14}{size:>11}{bpp:>8.3f}")
    say("")
    say("  Calibrate the threshold against these before it gates anything.")
    out["proxySuspects"] = len(suspects)

    # Screenshot inference
    say.head("Screenshot classification feasibility")
    shots = [a for a in assets
             if (a.get("uti") or "").endswith("png")
             and (a.get("width"), a.get("height")) in SCREEN_SIZES]
    pngs = [a for a in assets if (a.get("uti") or "").endswith("png")]
    say(f"  PNG assets                {len(pngs):,}")
    say(f"  PNG at a known screen size {len(shots):,}")
    say(f"  total screenshot bytes    {human_bytes(sum(a.get('fileSize') or 0 for a in shots))}")
    unknown = Counter((a.get("width"), a.get("height")) for a in pngs
                      if (a.get("width"), a.get("height")) not in SCREEN_SIZES)
    if unknown:
        say("  PNG sizes not in the table (add these to SCREEN_SIZES):")
        for (w, h), n in unknown.most_common(8):
            say(f"    {w}x{h}  {n:,}")
    out["screenshots"] = len(shots)

    # Q7 hints
    say.head("Q7: interruption behaviour")
    say("  Not answerable from a clean run. Re-run this script and, while the")
    say("  catalog is building, lock the iPhone. Then run it again and unplug the")
    say("  cable. Both error paths are captured in the JSONL for comparison.")

    return out


# --------------------------------------------------------------------------
# ifuse cross-check
# --------------------------------------------------------------------------

def ifuse_crosscheck(say: Reporter, ic_count: int) -> dict:
    say.head("Q1b: independent AFC cross-check via ifuse")
    mount = Path("/tmp/iim-p0-mount")
    mount.mkdir(exist_ok=True)
    res: dict = {}
    try:
        r = subprocess.run(["ifuse", str(mount)], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            say(f"  mount failed: {(r.stderr or r.stdout).strip()[:200]}")
            say("  Skipping. This is a cross-check, not a blocker.")
            return {"mounted": False}
        dcim = mount / "DCIM"
        files = [p for p in dcim.rglob("*") if p.is_file()] if dcim.exists() else []
        total = sum(p.stat().st_size for p in files)
        ext = Counter(p.suffix.upper() for p in files)
        say(f"  files under /DCIM         {len(files):,}")
        say(f"  bytes under /DCIM         {human_bytes(total)}")
        say(f"  ImageCaptureCore assets   {ic_count:,}")
        delta = len(files) - ic_count
        say(f"  delta                     {delta:+,}")
        say("")
        for e, n in ext.most_common(12):
            say(f"    {e or '(none)':<10}{n:>8,}")
        if delta:
            say("")
            say("  A non-zero delta is expected: .AAE sidecars and Live Photo .MOV")
            say("  halves are counted differently by the two views. What matters is")
            say("  whether any ORIGINAL image is visible to one and not the other.")
        res = {"mounted": True, "afcFiles": len(files), "afcBytes": total,
               "icAssets": ic_count, "delta": delta, "byExtension": dict(ext)}
    except subprocess.TimeoutExpired:
        say("  ifuse timed out.")
        res = {"mounted": False}
    finally:
        subprocess.run(["umount", str(mount)], capture_output=True)
    return res


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timeout", type=int, default=1800,
                    help="seconds to wait for the content catalog (default 1800)")
    ap.add_argument("--compare-presentations", action="store_true",
                    help="also enumerate with converted assets, to measure the transcoding hazard")
    ap.add_argument("--no-ifuse", action="store_true", help="skip the AFC cross-check")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    say = Reporter()

    say(f"# P0 transport spike, {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    say("")
    say("Read-only. Nothing is downloaded, modified or deleted.")

    facts = preflight(say)

    say.head("Enumerating (original assets)")
    records, rc, elapsed = run_helper("original", args.timeout, say)
    (OUT_DIR / f"probe-original-{stamp}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n")
    say(f"  helper exit {rc} after {elapsed:.1f}s, {len(records):,} records")
    if rc != 0:
        say("")
        say("  The probe did not complete. Everything below is partial.")

    findings = analyse(records, say, facts)
    findings["helperExit"] = rc
    findings["elapsedSeconds"] = round(elapsed, 1)

    if args.compare_presentations and rc == 0:
        say.head("Q1c: original vs converted presentation")
        say("  This measures the transcoding hazard: by default ImageCaptureCore")
        say("  hands out JPEG transcodes of HEIC originals, not the originals.")
        conv, crc, celapsed = run_helper("converted", args.timeout, say)
        (OUT_DIR / f"probe-converted-{stamp}.jsonl").write_text(
            "\n".join(json.dumps(r) for r in conv) + "\n")
        ca = [r for r in conv if r.get("event") == "asset"]
        oa = [r for r in records if r.get("event") == "asset"]
        ob = sum(a.get("fileSize") or 0 for a in oa)
        cb = sum(a.get("fileSize") or 0 for a in ca)
        say("")
        say(f"  original   {len(oa):>8,} assets  {human_bytes(ob)}")
        say(f"  converted  {len(ca):>8,} assets  {human_bytes(cb)}")
        say(f"  difference {len(oa)-len(ca):>+8,} assets  {human_bytes(ob-cb)}")
        say("")
        say("  If converted is materially smaller, then failing to set")
        say("  mediaPresentation = .originalAssets silently backs up transcodes.")
        findings["presentationComparison"] = {
            "originalAssets": len(oa), "originalBytes": ob,
            "convertedAssets": len(ca), "convertedBytes": cb,
        }

    if not args.no_ifuse and facts.get("macfuse") and rc == 0:
        findings["ifuse"] = ifuse_crosscheck(say, findings.get("assetCount", 0))

    say.head("Next")
    say("  1. Read the numbers above against docs/PLAN.md section 2.")
    say("  2. Re-run twice more to answer Q7: once locking the phone mid-catalog,")
    say("     once unplugging the cable.")
    say("  3. Update docs/PLAN.md with what was observed, then P1 can start.")

    json_path = OUT_DIR / f"findings-{stamp}.json"
    md_path = OUT_DIR / f"findings-{stamp}.md"
    json_path.write_text(json.dumps(findings, indent=2, default=str))
    md_path.write_text("\n".join(say.lines) + "\n")
    print()
    print(f"Findings written to:\n  {md_path}\n  {json_path}")
    return 0 if rc == 0 else rc


if __name__ == "__main__":
    sys.exit(main())
