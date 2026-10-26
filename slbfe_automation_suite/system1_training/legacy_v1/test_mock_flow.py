"""
End-to-end test against the local mock SLBFE server.
Starts the mock server, runs the REAL training_checker logic in headless
Chromium, and verifies every branch of the fallback / parsing logic.
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
from playwright.sync_api import sync_playwright

import training_checker as tc
from mock_slbfe_server import Handler

MOCK_URL = "http://127.0.0.1:8765/TraRegChk.asp"
WORKDIR = Path(__file__).resolve().parent / "mock_workspace"

TEST_PEOPLE = [
    # 1. training + registration found on first identifier (the screenshot case)
    {"no": "1", "name": "ROMANIA WORKER", "current_passport": "N8074311",
     "current_nic": "980260925V", "previous_passport": "", "previous_nic": ""},
    # 2. current passport & NIC empty on the server; OLD passport has training
    {"no": "2", "name": "OLD PP WORKER", "current_passport": "N11030886",
     "current_nic": "820453352V", "previous_passport": "N5408869",
     "previous_nic": ""},
    # 3. registration only, no training anywhere
    {"no": "3", "name": "REG ONLY", "current_passport": "R1234567",
     "current_nic": "900000000V", "previous_passport": "", "previous_nic": ""},
    # 4. nothing found under any identifier
    {"no": "4", "name": "NOBODY", "current_passport": "N0000001",
     "current_nic": "999999999V", "previous_passport": "N0000002",
     "previous_nic": ""},
    # 5. blacklisted but training still visible
    {"no": "5", "name": "BLACKLISTED PERSON", "current_passport": "B1000001",
     "current_nic": "911111111V", "previous_passport": "", "previous_nic": ""},
    # 6. short first identifier triggers JS alert + fallback submit,
    #    then training is found via the previous passport
    {"no": "6", "name": "ALERT THEN FOUND", "current_passport": "SHORT",
     "current_nic": "922222222V", "previous_passport": "N8074311",
     "previous_nic": ""},
]


class Args:
    delay = 0.2
    timeout = 8
    output_dir = WORKDIR / "output"


def main():
    if WORKDIR.exists():
        shutil.rmtree(WORKDIR)
    (WORKDIR / "output" / "errors").mkdir(parents=True)

    with open(WORKDIR / "people.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(TEST_PEOPLE[0].keys()))
        w.writeheader()
        w.writerows(TEST_PEOPLE)

    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    tc.SEARCH_URL = MOCK_URL  # point the real checker at the mock server

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        assert tc.session_is_valid(page) is False  # about:blank initially
        page.goto(MOCK_URL)
        assert tc.session_is_valid(page) is True   # search form = valid session

        results = []
        for person in TEST_PEOPLE:
            print(f"\n--- No.{person['no']} {person['name']} ---")
            r = tc.process_person(page, person, Args())
            results.append(r)
        browser.close()
    server.shutdown()

    tc.write_reports(results, Args.output_dir)

    by_no = {r.no: r for r in results}

    # 1. screenshot case: training + registration, first identifier
    r1 = by_no["1"]
    assert r1.status == "TRAINING_FOUND", r1.status
    assert r1.matched_identifier == "Current Passport: N8074311"
    assert r1.training_records[0]["name"] == "Romania Pre departure Training"
    assert r1.training_records[0]["date"] == "14/10/2026"
    assert len(r1.registration_records) == 1
    assert r1.registration_records[0]["country"] == "Qatar"

    # 2. fallback chain reaches the previous passport
    r2 = by_no["2"]
    assert r2.status == "TRAINING_FOUND"
    assert r2.matched_identifier == "Previous Passport: N5408869"
    assert len(r2.identifiers_tried) == 3
    assert r2.training_records[0]["date"] == "31/08/2026"

    # 3. registration only
    r3 = by_no["3"]
    assert r3.status == "REGISTRATION_ONLY"
    assert len(r3.registration_records) == 1

    # 4. no record
    r4 = by_no["4"]
    assert r4.status == "NO_RECORD"
    assert len(r4.identifiers_tried) == 3

    # 5. blacklist flag + training
    r5 = by_no["5"]
    assert r5.status == "TRAINING_FOUND"
    assert r5.blacklisted is True

    # 6. alert dismissed, fallback submit, eventually found
    r6 = by_no["6"]
    assert r6.status == "TRAINING_FOUND", r6.status
    assert r6.matched_identifier == "Previous Passport: N8074311"

    print("\n\nAll end-to-end assertions passed.")
    print(f"Reports written under {Args.output_dir}")


if __name__ == "__main__":
    main()
