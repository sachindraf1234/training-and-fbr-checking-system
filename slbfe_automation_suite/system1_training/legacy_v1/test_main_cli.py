"""
Runs the real CLI entry point (training_checker.main) against the mock server,
verifying argument parsing, run_log.jsonl creation and the resume feature.
"""
import csv
import shutil
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from http.server import ThreadingHTTPServer
import training_checker as tc
from mock_slbfe_server import Handler

WORKDIR = ROOT / "tests" / "cli_workspace"


def write_people():
    people = [
        {"no": "1", "name": "ROMANIA WORKER", "current_passport": "N8074311",
         "current_nic": "980260925V", "previous_passport": "", "previous_nic": ""},
        {"no": "2", "name": "OLD PP WORKER", "current_passport": "N11030886",
         "current_nic": "820453352V", "previous_passport": "N5408869",
         "previous_nic": ""},
        {"no": "4", "name": "NOBODY", "current_passport": "N0000001",
         "current_nic": "999999999V", "previous_passport": "N0000002",
         "previous_nic": ""},
    ]
    with open(WORKDIR / "people.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(people[0].keys()))
        w.writeheader()
        w.writerows(people)


def run_cli(*extra):
    sys.argv = ["training_checker.py",
                "--input", str(WORKDIR / "people.csv"),
                "--state", str(WORKDIR / "session_state.json"),
                "--outdir", str(WORKDIR / "output"),
                "--headless", "--delay", "0.1", "--timeout", "8", *extra]
    tc.main()


def main():
    if WORKDIR.exists():
        shutil.rmtree(WORKDIR)
    (WORKDIR / "output").mkdir(parents=True)
    write_people()

    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    tc.SEARCH_URL = "http://127.0.0.1:8765/TraRegChk.asp"

    print("\n########## FIRST RUN (limit 2) ##########")
    run_cli("--limit", "2")
    log = WORKDIR / "output" / "run_log.jsonl"
    assert log.exists(), "run_log.jsonl was not created"
    first_lines = [l for l in log.read_text().splitlines() if l.strip()]
    assert len(first_lines) == 2, f"expected 2 log lines, got {len(first_lines)}"

    print("\n########## SECOND RUN (full list - should resume) ##########")
    run_cli()
    second_lines = [l for l in log.read_text().splitlines() if l.strip()]
    assert len(second_lines) == 3, f"expected 3 log lines, got {len(second_lines)}"

    print("\n########## THIRD RUN (--no-resume, --only 4) ##########")
    run_cli("--no-resume", "--only", "4")
    lines = [l for l in log.read_text().splitlines() if l.strip()]
    assert len(lines) == 1, f"--no-resume should rebuild log, got {len(lines)}"

    report = (WORKDIR / "output" / "training_report.csv").read_text()
    assert "NO_RECORD" in report and "NOBODY" in report
    print("\nAll CLI / resume / logging tests passed.")
    server.shutdown()


if __name__ == "__main__":
    main()
