"""
training_checker.py
===================
Automatic Training & Experience checking on the SLBFE agency portal
(https://applications.slbfe.lk  ->  View -Training / Experience /Blacklist)

For every worker in people.csv the script tries, in order:
    1. Current Passport Number
    2. Current NIC Number
    3. Previous (old) Passport Number
    4. Previous (old) NIC Number
until it finds the worker's TRAINING records (the highlighted lines such as
"PP N8074311 NIC 980260925V Training~ Romania Pre departure Training 14/10/2026").
Registration records and Blacklist warnings are captured as well.

Outputs (in the output/ folder):
    training_report.csv   - one row per worker (status, training, registration...)
    training_records.csv  - one row per individual training record
    run_log.jsonl         - raw machine-readable log, also used for resume

First run (a visible browser opens so you can log in once):
    python training_checker.py --limit 3          # test with 3 workers first
    python training_checker.py                    # full run, resumes automatically
    python training_checker.py --headless         # after the session is saved
"""

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# --------------------------------------------------------------------------- #
# Site configuration (from the page-source / network analysis of TraRegChk.asp)
# --------------------------------------------------------------------------- #
SEARCH_URL = ("https://applications.slbfe.lk/logroot/agency/"
              "training/age_training/TraRegChk.asp")

READER_INPUT = 'input[name="reader"]'          # visible field you type into
SUBMIT_BUTTON = 'input[name="Submit"]'         # S E A R C H
RESULT_OPTIONS = 'select[name="idindex"] option'
BLACKLIST_LINK = 'a[href*="wrongwayb.asp"]'

# Example option texts returned by the server:
#  PP N8074311 NIC 980260925V Training~ Romania Pre departure Training 14/10/2026
#  PP N8074311 NIC 980260925V Registration~ > 27/06/2023 | Qatar | Specialist Marketing | Source:...
RECORD_RE = re.compile(
    r"^\s*PP\s+(?P<pp>\S+)\s+NIC\s+(?P<nic>\S+)\s+"
    r"(?P<kind>Training|Registration)~\s*(?P<body>.*)$",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"(?P<date>\d{2}/\d{2}/\d{4})\s*$")

IDENTIFIER_ORDER = [
    ("current_passport", "Current Passport"),
    ("current_nic", "Current NIC"),
    ("previous_passport", "Previous Passport"),
    ("previous_nic", "Previous NIC"),
]


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #
@dataclass
class ParsedRecord:
    kind: str                 # Training | Registration | Other
    pp: str = ""
    nic: str = ""
    name: str = ""            # training name / registration job category
    date: str = ""
    country: str = ""
    source: str = ""
    raw_text: str = ""


@dataclass
class PersonResult:
    no: str
    name: str
    status: str = "PENDING"                 # TRAINING_FOUND / REGISTRATION_ONLY /
                                            # NO_RECORD / INVALID_IDENTIFIERS / ERROR
    matched_identifier: str = ""            # e.g. "Current Passport: N8074311"
    identifiers_tried: list = field(default_factory=list)
    blacklisted: bool = False
    training_records: list = field(default_factory=list)
    registration_records: list = field(default_factory=list)
    other_records: list = field(default_factory=list)
    notes: str = ""


class SessionExpiredError(RuntimeError):
    """Raised when the portal redirects to the 'session expired' page."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def load_people(csv_path: Path):
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def candidate_identifiers(person: dict):
    """Return the ordered, de-duplicated list of (column, label, value) to try."""
    candidates = []
    seen = set()
    for column, label in IDENTIFIER_ORDER:
        value = (person.get(column) or "").strip().upper()
        if not value or value in {"-", "—"}:
            continue
        if value in seen:
            continue
        seen.add(value)
        candidates.append((column, label, value))
    return candidates


def parse_option(text: str) -> ParsedRecord:
    text = re.sub(r"\s+", " ", text.strip())
    m = RECORD_RE.match(text)
    if not m:
        return ParsedRecord(kind="Other", raw_text=text)

    kind = m.group("kind").capitalize()
    body = re.sub(r"\s+", " ", m.group("body").strip())
    rec = ParsedRecord(kind=kind, pp=m.group("pp"), nic=m.group("nic"), raw_text=text)

    if kind == "Training":
        dm = DATE_RE.search(body)
        if dm:
            rec.date = dm.group("date")
            rec.name = body[: dm.start()].strip()
        else:
            rec.name = body
    else:  # Registration: "> 27/06/2023 | Qatar | Specialist Marketing | Source:..."
        body = body.lstrip("> ").strip()
        pieces = [p.strip() for p in body.split("|")]
        if pieces:
            rec.date = pieces[0]
        if len(pieces) > 1:
            rec.country = pieces[1]
        if len(pieces) > 2:
            rec.name = pieces[2]
        if len(pieces) > 3:
            rec.source = pieces[3]
    return rec


# --------------------------------------------------------------------------- #
# Browser / session handling
# --------------------------------------------------------------------------- #
def session_is_valid(page) -> bool:
    url = page.url or ""
    if "wrongway" in url.lower() or "gohome" in url.lower():
        return False
    try:
        return page.locator(READER_INPUT).count() > 0
    except Exception:
        return False


def wait_for_manual_login(page, state_path: Path, timeout_seconds: int = 600):
    """Open the portal and wait until the user has logged in and reached the
    Training & Experience Inquiry page, then persist the session cookies."""
    print("\n" + "=" * 70)
    print("LOGIN REQUIRED")
    print("A browser window is open. Please:")
    print("  1. Log in to the SLBFE agency portal with your own credentials.")
    print("  2. Open the menu item: View -Training / Experience /Blacklist")
    print("     (the 'Training & Experience Inquiry' page with the search box).")
    print("This script continues automatically as soon as that page appears.")
    print("=" * 70 + "\n")
    page.goto(SEARCH_URL, wait_until="domcontentloaded")

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if session_is_valid(page):
            page.context.storage_state(path=str(state_path))
            print(f"[session] Login detected - session saved to {state_path}\n")
            return
        time.sleep(2)
    raise SystemExit("Login was not completed within the time limit. Re-run the script.")


def ensure_session(page, context, state_path: Path, headed: bool):
    page.goto(SEARCH_URL, wait_until="domcontentloaded")
    if session_is_valid(page):
        return
    if not headed:
        raise SystemExit(
            "Session expired and --headless mode cannot show a login window.\n"
            "Re-run once without --headless to log in again."
        )
    wait_for_manual_login(page, state_path)


# --------------------------------------------------------------------------- #
# One search on the portal
# --------------------------------------------------------------------------- #
def search_identifier(page, identifier: str, timeout_ms: int, screenshot_dir: Path):
    """Fill the inquiry form for one identifier and return the parsed page."""
    alert_message = {"text": None}

    def on_dialog(dialog):
        alert_message["text"] = dialog.message
        dialog.dismiss()

    page.on("dialog", on_dialog)
    try:
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_selector(READER_INPUT, timeout=timeout_ms)
        page.fill(READER_INPUT, identifier.upper())

        # Normal submit (the site's chk() copies reader -> ppno and validates)
        page.click(SUBMIT_BUTTON)
        try:
            page.wait_for_selector(
                f"{RESULT_OPTIONS}, {READER_INPUT}", timeout=timeout_ms
            )
        except PlaywrightTimeoutError:
            pass

        # Session expired mid-run?
        if "wrongway" in (page.url or "").lower():
            raise SessionExpiredError(page.url)

        # The JS validation may have blocked submit with an alert and CLEARED
        # the field. Re-inject the identifier and submit directly, bypassing
        # the client-side check so the server still receives the real value.
        if page.locator(READER_INPUT).count() > 0 and \
                page.locator(RESULT_OPTIONS).count() == 0:
            if alert_message["text"]:
                page.evaluate(
                    "(v) => { document.form1.reader.value = v;"
                    " document.form1.ppno.value = v;"
                    " document.form1.action.value = 'chk';"
                    " document.form1.submit(); }",
                    identifier.upper(),
                )
                try:
                    page.wait_for_selector(
                        f"{RESULT_OPTIONS}, {READER_INPUT}", timeout=timeout_ms
                    )
                except PlaywrightTimeoutError:
                    pass
                if "wrongway" in (page.url or "").lower():
                    raise SessionExpiredError(page.url)

        blacklisted = page.locator(BLACKLIST_LINK).count() > 0
        option_texts = [
            t.strip() for t in page.locator(RESULT_OPTIONS).all_inner_texts()
            if t.strip()
        ]
        records = [parse_option(t) for t in option_texts]

        snippet = ""
        if not records:
            try:
                snippet = re.sub(r"\s+", " ", page.inner_text("body")).strip()[:200]
            except Exception:
                pass
        return {
            "records": records,
            "blacklisted": blacklisted,
            "alert": alert_message["text"],
            "snippet": snippet,
        }
    except SessionExpiredError:
        raise
    except Exception as exc:
        shot = screenshot_dir / f"error_{identifier}_{int(time.time())}.png"
        try:
            page.screenshot(path=str(shot), full_page=True)
        except Exception:
            shot = None
        raise RuntimeError(f"Search failed for {identifier}: {exc} "
                           f"(screenshot: {shot})") from exc
    finally:
        page.remove_listener("dialog", on_dialog)


# --------------------------------------------------------------------------- #
# Process one worker (try every available identifier)
# --------------------------------------------------------------------------- #
def process_person(page, person: dict, args) -> PersonResult:
    result = PersonResult(no=person["no"], name=person["name"])
    candidates = candidate_identifiers(person)

    if not candidates:
        result.status = "INVALID_IDENTIFIERS"
        result.notes = "No passport/NIC number provided in the input file."
        return result

    for column, label, value in candidates:
        tag = f"{label}: {value}"
        result.identifiers_tried.append(tag)
        print(f"    -> trying {tag} ...", flush=True)

        data = search_identifier(
            page, value, timeout_ms=args.timeout * 1000,
            screenshot_dir=args.output_dir / "errors",
        )
        result.blacklisted = result.blacklisted or data["blacklisted"]
        records = data["records"]

        trainings = [r for r in records if r.kind == "Training"]
        registrations = [r for r in records if r.kind == "Registration"]
        others = [r for r in records if r.kind == "Other"]
        # de-duplicate by raw text in case two identifiers return the same data
        known_reg = {x["raw_text"] for x in result.registration_records}
        for r in registrations:
            d = asdict(r)
            if d["raw_text"] not in known_reg:
                result.registration_records.append(d)
                known_reg.add(d["raw_text"])
        known_other = {x["raw_text"] for x in result.other_records}
        for r in others:
            d = asdict(r)
            if d["raw_text"] not in known_other:
                result.other_records.append(d)
                known_other.add(d["raw_text"])

        if trainings:
            result.status = "TRAINING_FOUND"
            result.matched_identifier = tag
            result.training_records = [asdict(r) for r in trainings]
            print(f"       TRAINING FOUND ({len(trainings)} record(s)) via {tag}")
            return result

        if records:
            print(f"       {len(records)} non-training record(s); "
                  f"trying next identifier...")
        else:
            reason = f"alert={data['alert']}" if data["alert"] else \
                     (data["snippet"][:80] if data["snippet"] else "no records")
            print(f"       no records ({reason}); trying next identifier...")
        time.sleep(args.delay)

    # Tried every identifier without finding training
    if result.registration_records:
        result.status = "REGISTRATION_ONLY"
        result.notes = "Only registration records found - no training record."
    elif result.other_records:
        result.status = "OTHER_RECORDS"
    else:
        result.status = "NO_RECORD"
        result.notes = "No training/registration data under any given identifier."
    return result


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def write_reports(results: list, output_dir: Path):
    # keep the report in the same order as the worker list (numeric "No")
    results = sorted(results, key=lambda r: int(r.no) if r.no.isdigit() else 999999)
    report_path = output_dir / "training_report.csv"
    records_path = output_dir / "training_records.csv"

    with open(report_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "No", "Name", "Status", "Matched Identifier", "Identifiers Tried",
            "Blacklisted", "Training Count", "Training Details",
            "Registration Count", "Registration Details", "Notes",
        ])
        for r in results:
            training_details = " || ".join(
                f"{t['name']} ({t['date']})".strip() for t in r.training_records
            )
            reg_details = " || ".join(
                " | ".join(part for part in
                           [x["date"], x["country"], x["name"], x["source"]]
                           if part)
                for x in r.registration_records
            )
            w.writerow([
                r.no, r.name, r.status, r.matched_identifier,
                " ; ".join(r.identifiers_tried), "YES" if r.blacklisted else "NO",
                len(r.training_records), training_details,
                len(r.registration_records), reg_details, r.notes,
            ])

    with open(records_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["No", "Name", "Matched Identifier", "Record PP", "Record NIC",
                    "Training Name", "Training Date", "Raw Record Text"])
        for r in results:
            for t in r.training_records:
                w.writerow([
                    r.no, r.name, r.matched_identifier, t["pp"], t["nic"],
                    t["name"], t["date"], t["raw_text"],
                ])


def load_run_log(log_path: Path):
    if not log_path.exists():
        return {}
    done = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        done[str(obj["no"])] = PersonResult(**obj)
    return done


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="SLBFE Training & Experience checker")
    ap.add_argument("--input", default="people.csv", help="Worker list CSV")
    ap.add_argument("--state", default="session_state.json",
                    help="Saved browser session file")
    ap.add_argument("--outdir", default="output", help="Report output folder")
    ap.add_argument("--delay", type=float, default=1.5,
                    help="Seconds to wait between searches (polite rate limit)")
    ap.add_argument("--timeout", type=int, default=30,
                    help="Per-page timeout in seconds")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process only the first N workers (0 = all)")
    ap.add_argument("--only", default="",
                    help="Comma-separated row numbers, e.g. 2,14,137")
    ap.add_argument("--headless", action="store_true",
                    help="Run without a visible browser (needs a saved session)")
    ap.add_argument("--no-resume", action="store_true",
                    help="Ignore run_log.jsonl and start from the beginning")
    args = ap.parse_args()

    base = Path(__file__).parent
    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = base / input_path
    state_path = Path(args.state)
    if not state_path.is_absolute():
        state_path = base / state_path
    args.output_dir = Path(args.outdir)
    if not args.output_dir.is_absolute():
        args.output_dir = base / args.output_dir
    (args.output_dir / "errors").mkdir(parents=True, exist_ok=True)
    log_path = args.output_dir / "run_log.jsonl"

    people = load_people(input_path)
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        people = [p for p in people if p["no"] in wanted]
    if args.limit:
        people = people[: args.limit]

    completed = {} if args.no_resume else load_run_log(log_path)
    # successful rows are resumed; rows that ended in ERROR are retried
    todo = [p for p in people
            if p["no"] not in completed or completed[p["no"]].status == "ERROR"]
    print(f"Total workers: {len(people)} | already done: {len(completed)} | "
          f"to process now: {len(todo)}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless)
        if state_path.exists():
            context = browser.new_context(storage_state=str(state_path))
        else:
            context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(args.timeout * 1000)

        ensure_session(page, context, state_path, headed=not args.headless)
        results = list(completed.values())

        # --no-resume starts a fresh run: truncate the old log instead of mixing runs
        log_fh = open(log_path, "w" if args.no_resume else "a", encoding="utf-8")
        try:
            for idx, person in enumerate(todo, start=1):
                print(f"[{idx}/{len(todo)}] No.{person['no']} {person['name']}")
                try:
                    result = process_person(page, person, args)
                except SessionExpiredError:
                    print("  [session] expired - please log in again in the browser...")
                    ensure_session(page, context, state_path,
                                   headed=not args.headless)
                    result = process_person(page, person, args)  # one retry
                except Exception as exc:
                    result = PersonResult(no=person["no"], name=person["name"],
                                          status="ERROR", notes=str(exc))
                    print(f"  ERROR: {exc}")

                completed[result.no] = result
                results = list(completed.values())
                log_fh.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
                log_fh.flush()
                write_reports(results, args.output_dir)
                time.sleep(args.delay)
        finally:
            log_fh.close()
            browser.close()

    results = list(completed.values())
    write_reports(results, args.output_dir)
    summary = {}
    for r in results:
        summary[r.status] = summary.get(r.status, 0) + 1
    print("\nRun complete. Summary:")
    for status, count in sorted(summary.items()):
        print(f"  {status:22s} {count}")
    print(f"\nReports:\n  {args.output_dir / 'training_report.csv'}"
          f"\n  {args.output_dir / 'training_records.csv'}")


if __name__ == "__main__":
    sys.exit(main())
