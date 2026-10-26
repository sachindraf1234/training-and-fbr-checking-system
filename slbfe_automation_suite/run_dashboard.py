#!/usr/bin/env python3
"""
SLBFE Automation Suite - common dashboard
=========================================
One entry point for TWO fully independent automation systems:

  SYSTEM 1  system1_training/  - Pre Departure Training checker (all applicants)
  SYSTEM 2  system2_fbr/       - Family Background Report checker (FEMALE only)

The systems never share code, input files, browser sessions or output folders.
This dashboard only LAUNCHES each system in its own sub-process (with the
working directory set to that system's folder) and READS their JSON results to
show one combined status view. Nothing here joins the two systems' data.

Interactive use:
    python run_dashboard.py

Scripted / scheduled use:
    python run_dashboard.py --system 1                 # run System 1
    python run_dashboard.py --system 2 -- --headless  # extra flags after --
    python run_dashboard.py --overview                 # combined status in terminal
    python run_dashboard.py --html                     # build combined_dashboard.html
    python run_dashboard.py --test                     # run both self-test suites
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SUITE_DIR = Path(__file__).resolve().parent

SYSTEMS = {
    1: {
        "name": "System 1 - Pre Departure Training check",
        "dir": SUITE_DIR / "system1_training",
        "script": "slbfe_training_checker.py",
        "input": "name.txt",
        "summary": None,  # progress.json holds the state
        "state": "progress.json",
        "audience": "All applicants",
    },
    2: {
        "name": "System 2 - Female FBR (Family Background Report) check",
        "dir": SUITE_DIR / "system2_fbr",
        "script": "fbr_checker.py",
        "input": "name_female.txt",
        "summary": "output/fbr_summary.json",
        "state": "slbfe_session.json",
        "audience": "Female applicants only",
    },
}


# --------------------------------------------------------------------------- #
# Launching (each system runs as an independent process in its own folder)
# --------------------------------------------------------------------------- #
def run_system(no: int, extra: list[str] | None = None) -> int:
    cfg = SYSTEMS[no]
    script = cfg["dir"] / cfg["script"]
    if not script.exists():
        print(f"ERROR: {script} not found.")
        return 1
    cmd = [sys.executable, str(script), *(extra or [])]
    print("\n" + "#" * 72)
    print(f"# LAUNCHING {cfg['name']}")
    print(f"# cwd : {cfg['dir']}")
    print(f"# cmd : {' '.join(shlex.quote(c) for c in cmd)}")
    print("#" * 72 + "\n")
    # separate cwd => each system keeps its own inputs/outputs/session files
    return subprocess.call(cmd, cwd=str(cfg["dir"]))


def run_tests() -> int:
    rc = 0
    tests = [
        (SYSTEMS[1]["dir"], ["python", "tests/test_v2_flow.py"]),
        (SYSTEMS[2]["dir"], ["python", "tests/test_fbr_flow.py"]),
    ]
    for cwd, cmd in tests:
        print("\n" + "=" * 72)
        print(f"TESTS in {cwd.name}: {' '.join(cmd)}")
        print("=" * 72)
        rc |= subprocess.call([sys.executable if cmd[0] == "python" else cmd[0], *cmd[1:]],
                              cwd=str(cwd))
    return rc


# --------------------------------------------------------------------------- #
# Reading each system's own result files (read-only aggregation for display)
# --------------------------------------------------------------------------- #
def read_system1() -> dict:
    state = SYSTEMS[1]["dir"] / "progress.json"
    if not state.exists():
        return {"available": False, "counts": {}, "rows": []}
    data = json.loads(state.read_text(encoding="utf-8"))
    rows = list(data.values())
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("status", "?")] = counts.get(r.get("status", "?"), 0) + 1
    return {"available": True, "counts": counts, "rows": rows,
            "updated": datetime.fromtimestamp(state.stat().st_mtime).strftime("%Y-%m-%d %H:%M")}


def read_system2() -> dict:
    summary = SYSTEMS[2]["dir"] / "output" / "fbr_summary.json"
    if not summary.exists():
        return {"available": False, "counts": {}, "rows": []}
    data = json.loads(summary.read_text(encoding="utf-8"))
    return {"available": True, "counts": data.get("counts", {}),
            "rows": data.get("results", []), "updated": data.get("generated_at", "")}


def print_overview() -> None:
    s1, s2 = read_system1(), read_system2()
    print("\n" + "=" * 72)
    print("COMBINED STATUS OVERVIEW (systems remain independent - display only)")
    print("=" * 72)
    for no, info in ((1, s1), (2, s2)):
        cfg = SYSTEMS[no]
        print(f"\n{cfg['name']}  [{cfg['audience']}]")
        if not info["available"]:
            print("   No run yet.")
            continue
        print(f"   updated: {info.get('updated', '?')} | records: {len(info['rows'])}")
        for k, v in sorted(info["counts"].items(), key=lambda kv: -kv[1]):
            print(f"     {k:<18} {v}")
    print()


# --------------------------------------------------------------------------- #
# Combined HTML dashboard (self-contained, no external resources)
# --------------------------------------------------------------------------- #
COLORS = {
    "TRAINED": "#2e7d32", "VALID": "#2e7d32",
    "NOT_TRAINED": "#c62828", "EXPIRED": "#c62828",
    "REVIEW": "#ef6c00", "EXPIRES SOON": "#f9a825",
    "INCOMPLETE": "#ef6c00", "MISMATCH": "#ef6c00", "CHECK": "#ef6c00",
    "NOT_FOUND": "#f9a825",
    "NOT FOUND": "#757575", "ERROR": "#c62828", "INVALID_FORMAT": "#757575",
}


def _badge(status: str) -> str:
    color = COLORS.get(status, "#455a64")
    return (f'<span style="background:{color};color:#fff;padding:2px 9px;border-radius:10px;'
            f'font-size:12px;white-space:nowrap">{status or "-"}</span>')


def _esc(v) -> str:
    return str(v if v is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_html() -> Path:
    s1, s2 = read_system1(), read_system2()
    out = SUITE_DIR / "combined_dashboard.html"

    def cards(info: dict) -> str:
        if not info["available"]:
            return '<p class="muted">No run recorded yet.</p>'
        return "".join(
            f'<div class="card"><div class="num">{v}</div><div class="lbl">{_esc(k)}</div></div>'
            for k, v in sorted(info["counts"].items(), key=lambda kv: -kv[1])
        )

    def table1(rows: list) -> str:
        if not rows:
            return ""
        head = ("<tr><th>No</th><th>Name</th><th>Status</th><th>Found by</th>"
                "<th>Training</th><th>Date</th><th>Blacklisted</th><th>Note</th></tr>")
        body = "".join(
            "<tr>"
            f"<td>{_esc(r.get('no'))}</td><td>{_esc(r.get('name'))}</td>"
            f"<td>{_badge(r.get('status'))}</td>"
            + "".join(f"<td>{_esc(c)}</td>" for c in (
                r.get("found_by"), r.get("training"), r.get("training_date"),
                r.get("blacklisted"), r.get("note")))
            + "</tr>"
            for r in sorted(rows, key=lambda r: int(r.get("no", 0)))
        )
        return f"<table>{head}{body}</table>"

    def table2(rows: list) -> str:
        if not rows:
            return ""
        head = ("<tr><th>No</th><th>Name</th><th>Expiry</th><th>Validation</th>"
                "<th>FBR Date</th><th>Expiry Date</th><th>Days Left</th><th>FBR Status</th>"
                "<th>Passport</th><th>NIC</th><th>Remark</th></tr>")
        body = ""
        for r in sorted(rows, key=lambda r: int(r.get("no", 0))):
            body += ("<tr>"
                     f"<td>{_esc(r.get('no'))}</td><td>{_esc(r.get('name'))}</td>"
                     f"<td>{_badge(r.get('expiry_state'))}</td>"
                     f"<td>{_badge(r.get('validation'))}</td>"
                     f"<td>{_esc(r.get('fbr_date'))}</td><td>{_esc(r.get('expiry_date'))}</td>"
                     f"<td>{_esc(r.get('days_left'))}</td><td>{_esc(r.get('fbr_status'))}</td>"
                     f"<td>{_esc(r.get('current_passport'))}</td><td>{_esc(r.get('current_nic'))}</td>"
                     f"<td>{_esc(r.get('remark'))}</td></tr>")
        return f"<table>{head}{body}</table>"

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>SLBFE Automation Suite - Combined Dashboard</title>
<style>
  body {{ font-family: Segoe UI, Arial, sans-serif; margin: 0; background:#f4f6fa; color:#1b2430; }}
  header {{ background:#7b1113; color:#ffd966; padding:18px 28px; }}
  header h1 {{ margin:0; font-size:22px; }} header p {{ margin:6px 0 0; font-size:13px; color:#f1d7d7; }}
  main {{ padding:22px 28px; max-width:1400px; margin:auto; }}
  .panel {{ background:#fff; border-radius:10px; box-shadow:0 1px 4px rgba(0,0,0,.12); padding:18px 20px; margin-bottom:26px; }}
  .panel h2 {{ margin:0 0 4px; font-size:18px; }} .sub {{ color:#667; font-size:12px; margin-bottom:14px; }}
  .cards {{ display:flex; flex-wrap:wrap; gap:12px; margin-bottom:16px; }}
  .card {{ background:#f0f3f8; border-radius:8px; padding:10px 16px; min-width:96px; text-align:center; }}
  .num {{ font-size:24px; font-weight:700; }} .lbl {{ font-size:12px; color:#556; }}
  table {{ border-collapse:collapse; width:100%; font-size:12.5px; }}
  th, td {{ border:1px solid #dde3ec; padding:6px 8px; text-align:left; vertical-align:top; }}
  th {{ background:#1f3864; color:#fff; position:sticky; top:0; }}
  tr:nth-child(even) td {{ background:#fafbfd; }}
  .muted {{ color:#778; }} .links a {{ color:#1f4e9c; margin-right:14px; font-size:13px; }}
</style></head><body>
<header><h1>SLBFE Automation Suite - Combined Dashboard</h1>
<p>Two independent systems under one view &middot; generated {datetime.now():%Y-%m-%d %H:%M}</p></header>
<main>
  <section class="panel">
    <h2>System 1 - Pre Departure Training check <small style="font-weight:400;color:#667">(all applicants)</small></h2>
    <div class="sub">updated: {_esc(s1.get('updated', 'never run'))}</div>
    <div class="cards">{cards(s1)}</div>
    <div class="links">
      <a href="system1_training/training_results.xlsx">training_results.xlsx</a>
      <a href="system1_training/progress.json">progress.json</a>
      <span class="muted">proof screenshots: system1_training/screenshots/ folder</span>
    </div>
    {table1(s1.get('rows', []))}
  </section>
  <section class="panel">
    <h2>System 2 - Female FBR Verification <small style="font-weight:400;color:#667">(female applicants only)</small></h2>
    <div class="sub">updated: {_esc(s2.get('updated', 'never run'))}</div>
    <div class="cards">{cards(s2)}</div>
    <div class="links">
      <a href="system2_fbr/output/fbr_report_latest.xlsx">fbr_report_latest.xlsx</a>
      <a href="system2_fbr/output/fbr_report_latest.csv">fbr_report_latest.csv</a>
      <a href="system2_fbr/output/fbr_summary.json">fbr_summary.json</a>
    </div>
    {table2(s2.get('rows', []))}
  </section>
</main></body></html>"""
    out.write_text(html, encoding="utf-8")
    return out


# --------------------------------------------------------------------------- #
# Interactive menu
# --------------------------------------------------------------------------- #
def menu() -> None:
    while True:
        print("\n" + "=" * 72)
        print(" SLBFE AUTOMATION SUITE - DASHBOARD")
        print("=" * 72)
        print(" 1. Run SYSTEM 1 - Pre Departure Training check (all applicants)")
        print(" 2. Run SYSTEM 2 - Female FBR check (female only)")
        print(" 3. Combined status overview (terminal)")
        print(" 4. Build combined_dashboard.html")
        print(" 5. Run both self-test suites")
        print(" 0. Exit")
        choice = input("Choose: ").strip()
        if choice == "1" or choice == "2":
            no = int(choice)
            raw = input(f"Optional flags for {SYSTEMS[no]['name']} (blank for none): ").strip()
            extra = shlex.split(raw)
            run_system(no, extra)
        elif choice == "3":
            print_overview()
        elif choice == "4":
            path = build_html()
            print(f"Written: {path}")
        elif choice == "5":
            run_tests()
        elif choice == "0":
            print("Bye.")
            return
        else:
            print("Invalid choice.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Common dashboard for the two independent SLBFE systems")
    ap.add_argument("--system", type=int, choices=(1, 2), help="run one system and exit")
    ap.add_argument("--overview", action="store_true", help="print combined status and exit")
    ap.add_argument("--html", action="store_true", help="build combined_dashboard.html and exit")
    ap.add_argument("--test", action="store_true", help="run both self-test suites")
    ap.add_argument("passthrough", nargs="*", help="extra flags for --system (use after --)")
    args = ap.parse_args()

    if args.system:
        extra = args.passthrough
        if extra and extra[0] == "--":
            extra = extra[1:]
        sys.exit(run_system(args.system, extra))
    if args.overview:
        print_overview()
        return
    if args.html:
        print("Written:", build_html())
        return
    if args.test:
        sys.exit(run_tests())
    menu()


if __name__ == "__main__":
    main()
