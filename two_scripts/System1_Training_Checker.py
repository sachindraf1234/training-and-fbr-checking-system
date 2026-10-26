#!/usr/bin/env python3
"""
SLBFE "Training & Experience Inquiry" bulk checker  (Python + Playwright)

WHAT IT DOES
  For every applicant in name.txt it searches the portal with
      1. Current Passport   2. Current NIC   3. Previous Passport   4. Previous NIC
  (blank / "-" numbers are skipped) and looks through ALL returned records for
  the course that proves Pre Departure Training was completed:

        "Romania Pre departure Orientation Programme"

  Any Training record containing "Pre departure Orientation" (case-insensitive)
  counts as TRAINED.  Other courses (e.g. "Orientation for other than Domestic
  Housekeeping-2 days", "5 Day Training Programme for Female Cleaners",
  "Domestic Housekeeping-Middle East - 25 Days") do NOT qualify.

RESULT STATUSES (one per applicant)
  TRAINED      - Romania Pre departure Orientation Programme found under one of
                 the applicant's own passport/NIC numbers; a proof screenshot is
                 saved and the applicant is SKIPPED on every later run.
  NOT_TRAINED  - records exist but no Pre departure Orientation course.
  NOT_FOUND    - every searched number came back with the portal's yellow
                 "Not Found !!! The number you entered [...] not found" page.
                 A screenshot of that page is saved per number (proof that the
                 applicant has no record under that passport/NIC).
  REVIEW       - a qualifying course exists but its PP/NIC belong to someone else.
  INCOMPLETE   - a search failed (timeout / session loss / unknown page); re-run.

OUTPUTS (all in the script folder)
  training_results.xlsx  - ONE Excel workbook with two sheets:
                             "Applicants"  : one row per applicant (colour coded)
                             "Search Log"  : one row per passport/NIC search
  screenshots/           - proof screenshots, named with applicant row + name:
                             001_<Name>_TRAINED.png
                             001_<Name>_CurrentPassport_N8074311.png  (Not Found)
  progress.json          - run-to-run memory; TRAINED people are skipped next time
  debug/                 - raw dumps only if the portal returns an unknown page

SETUP (once)
    1_Install_Dependencies.bat        (or: pip install playwright openpyxl
                                       && python -m playwright install chromium)

RUN
    python System1_Training_Checker.py                  # normal run (skips TRAINED)
    python System1_Training_Checker.py --only 1-10,17   # only these row numbers
    python System1_Training_Checker.py --recheck-all    # ignore progress.json
    python System1_Training_Checker.py --notify-email   # also e-mail the list

LOGIN  (exact entry URL, never altered: https://services.slbfe.lk/account/ext_account_main)
  That page opens automatically when no valid session exists and shows all 3 login
  methods (Public Users / Authorized Personnel Only / Development Officer). For
  automatic credential entry the script clicks the 'Authorized Personnel Only ->
  Access' link to reach the Sign In form (usrLoginName / usrPassWord -> chkuser.asp).
  Three ways to authenticate, in order:
    1. saved session in slbfe_session.json (created automatically after first login)
    2. environment variables SLBFE_USER and SLBFE_PASS (fully automatic, --headless)
    3. type the username/password directly into the visible browser window; the
       script detects the successful login and continues by itself.
  In --headless mode without env vars it prompts for the credentials in the terminal.

EMAIL (optional, only with --notify-email) - set these environment variables first:
    SMTP_HOST  SMTP_PORT(587)  SMTP_USER  SMTP_PASS  NOTIFY_TO  [NOTIFY_FROM]
"""

import argparse
import getpass
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

PORTAL_URL = "https://applications.slbfe.lk/"
INQUIRY_URL = "https://applications.slbfe.lk/logroot/agency/training/age_training/TraRegChk.asp"

# Exact SLBFE login entry URL - opens the page with all 3 login methods
# (Public Users / Authorized Personnel Only / Development Officer).
# Keep this URL exactly as provided - do not deep-link past it.
LOGIN_URL = "https://services.slbfe.lk/account/ext_account_main"
# "Authorized Personnel Only" card -> its "Access" link (relative href on the page)
AUTH_ACCESS = 'a[href$="ext_account_"]'
LOGIN_USER = 'input[name="usrLoginName"]'
LOGIN_PASS = 'input[name="usrPassWord"]'
LOGIN_SUBMIT = 'button[type="submit"]'

SCRIPT_DIR = Path(__file__).resolve().parent
SESSION_FILE = SCRIPT_DIR / "slbfe_session.json"
USER_ENV, PASS_ENV = "SLBFE_USER", "SLBFE_PASS"

# The course that proves Pre Departure Training was completed.
# Real portal text: "Training~ Romania Pre departure Orientation Programme <date>"
# (matches "Pre departure Orientation" / "Pre-departure orientation" in any case)
TRAINED_MARKER_RE = re.compile(r"pre[\s\-]*departure\s+orientation", re.I)

ID_ORDER = [
    ("Current Passport", "cur_pp"),
    ("Current NIC", "cur_nic"),
    ("Previous Passport", "prev_pp"),
    ("Previous NIC", "prev_nic"),
]

# Record line example:
#   PP N8074311 NIC 980260925V Training~ Romania Pre departure Orientation Programme 14/10/2026
#   PP N8074311 NIC 980260925V Registration~ > 27/06/2023 | Qatar | Specialist Marketing | Source:...
RECORD_RE = re.compile(
    r"PP\s+(?P<pp>\S+)\s+NIC\s+(?P<nic>\S+)\s+(?P<kind>[A-Za-z ]+?)\s*[~\-:]\s*(?P<text>.*)$",
    re.S,
)
DATE_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")

EXCEL_FILE = "training_results.xlsx"
SUMMARY_COLS = ["no", "name", "status", "found_by", "training", "training_date",
                "ids_checked", "screenshots", "blacklisted", "note", "last_checked"]
DETAIL_COLS = ["time", "no", "name", "id_type", "id_searched", "status", "records", "note"]

STATUS_FILL = {
    "TRAINED": "C6E0B4",      # green
    "NOT_TRAINED": "F8CBAD",  # red
    "NOT_FOUND": "FFE699",    # amber
    "REVIEW": "FFE699",       # amber
    "INCOMPLETE": "D9D9D9",   # grey
}


# =========================================================================== input
def parse_people(path):
    people = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 6 or not cells[0].isdigit():
            continue

        def clean(v):
            v = v.replace("*", "").replace("\\", "").strip().upper()
            return "" if v in ("", "-") else v

        people.append({
            "no": int(cells[0]),
            "name": cells[1].replace("*", "").strip(),
            "cur_pp": clean(cells[2]), "cur_nic": clean(cells[3]),
            "prev_pp": clean(cells[4]), "prev_nic": clean(cells[5]),
        })
    return people


def parse_only(spec):
    if not spec:
        return None
    wanted = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            wanted.update(range(int(a), int(b) + 1))
        elif part:
            wanted.add(int(part))
    return wanted


def site_accepts(identifier):
    """Mirror of the site's own chk(): >= 8 chars and characters 2-8 numeric."""
    if len(identifier) < 8:
        return False
    try:
        float(identifier[1:8])
        return True
    except ValueError:
        return False


def person_key(p):
    return p["cur_pp"] or p["cur_nic"] or f"row{p['no']}"


def safe_filename(text, limit=90):
    text = re.sub(r"[^A-Za-z0-9 _\-]", "", text).strip()
    return re.sub(r"\s+", "_", text)[:limit]


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


# =========================================================================== records
def parse_record(raw):
    raw = norm(raw)
    m = RECORD_RE.search(raw)
    if not m:
        return {"pp": "", "nic": "", "kind": "", "text": raw, "date": ""}
    text = m["text"].strip()
    tail = re.search(r"(\d{1,2}/\d{1,2}/\d{4})\s*$", text)
    first = DATE_RE.search(text)
    return {
        "pp": m["pp"].upper(), "nic": m["nic"].upper(),
        "kind": m["kind"].strip(), "text": text,
        "date": (tail or first).group(1) if (tail or first) else "",
    }


def find_trained_record(records, known_ids):
    """Return (index, parsed_record, same_person) for the first record containing
    the 'Pre departure Orientation' course (the Romania PDO programme)."""
    review = None
    for i, raw in enumerate(records):
        rec = parse_record(raw)
        is_training = rec["kind"].lower().startswith("training") or "training" in raw.lower()
        if is_training and TRAINED_MARKER_RE.search(raw):
            same = bool({rec["pp"], rec["nic"]} & known_ids)
            if same:
                return i, rec, True
            review = review or (i, rec, False)
    return review  # None, or a record whose PP/NIC are not this person's


# =========================================================================== browser
def lookup(page, identifier, alerts, timeout_ms):
    out = {"status": "ERROR", "records": [], "blacklisted": "", "note": ""}
    page.goto(INQUIRY_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    reader = page.locator('input[name="reader"]')
    if reader.count() == 0:
        out.update(status="SESSION_EXPIRED", note="Search form not found - probably logged out.")
        return out

    alerts.clear()
    reader.fill(identifier)  # site's chk() copies reader -> ppno on submit
    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=timeout_ms):
            page.click('input[name="Submit"]')
    except PWTimeout:
        out.update(status="NO_RESPONSE", note="; ".join(alerts) or "Timed out waiting for result page.")
        return out

    # a redirect to wrongway.asp means the ASP session died mid-search
    if "wrongway" in (page.url or "").lower():
        out.update(status="SESSION_EXPIRED",
                   note="Session expired - log in again in the browser window.")
        return out

    body = norm(page.inner_text("body"))
    low = body.lower()

    # The portal's dedicated yellow "Not Found !!!" page:
    #   "The number you entered [P1243376 ] , not found !!!" + a RETURN (return2) button
    if (page.locator('input[name="return2"]').count() > 0
            or ("the number you entered" in low and "not found" in low)):
        out.update(status="NOT_FOUND",
                   note="Not Found page - no record on the portal for this number")
        return out

    opts = [norm(t) for t in page.locator('select[name="idindex"] option').all_inner_texts() if t.strip()]
    if not opts:
        if "session has expired" in low:
            out.update(status="SESSION_EXPIRED",
                       note="Session expired - log in again in the browser window.")
            return out
        if "Training & Experience Details" in body:
            # results page but the server returned zero records
            out.update(status="NOT_FOUND",
                       note="; ".join(alerts) or "No records returned for this identifier")
            return out

    bl = page.locator('a[href*="wrongwayb.asp"]')
    if bl.count():
        out["blacklisted"] = norm(bl.first.inner_text() or "BLACKLISTED")

    if opts:
        out.update(status="FOUND", records=opts)
    elif page.locator('input[name="reader"]').count():
        out.update(status="NOT_FOUND", note="; ".join(alerts))
    else:
        out.update(status="UNKNOWN_PAGE", note="Unrecognised result page (see debug/ folder)")
    return out


def take_proof_screenshot(page, option_index, path):
    """Highlight the matching line in the listbox, then save a full-page screenshot."""
    try:
        page.evaluate(
            """(i) => { const s = document.querySelector('select[name="idindex"]');
                        if (s && s.options[i]) { const o = s.options[i];
                          o.style.backgroundColor = '#ffff00'; o.style.fontWeight = 'bold';
                          o.scrollIntoView({block: 'nearest'}); } }""",
            option_index,
        )
    except Exception:
        pass
    page.screenshot(path=str(path), full_page=True)


# =========================================================================== files
def load_json(path, default):
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            p.rename(p.with_suffix(f".corrupt{int(time.time())}.json"))
    return default


def save_json(path, data):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def write_excel(path, summary_rows, detail_rows):
    """One workbook, two sheets: 'Applicants' (per person) and 'Search Log' (per ID)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="305496")

    ws = wb.active
    ws.title = "Applicants"
    ws.append(SUMMARY_COLS)
    for r in summary_rows:
        ws.append([r.get(c, "") for c in SUMMARY_COLS])
    for cell in ws[1]:
        cell.font = head_font
        cell.fill = head_fill
    status_col = SUMMARY_COLS.index("status") + 1
    for row in ws.iter_rows(min_row=2):
        fill = STATUS_FILL.get(row[status_col - 1].value)
        if fill:
            for cell in row:
                cell.fill = PatternFill("solid", fgColor=fill)
    widths = [6, 42, 12, 24, 46, 13, 34, 46, 26, 60, 20]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    ws2 = wb.create_sheet("Search Log")
    ws2.append(DETAIL_COLS)
    for r in detail_rows:
        ws2.append([r.get(c, "") for c in DETAIL_COLS])
    for cell in ws2[1]:
        cell.font = head_font
        cell.fill = head_fill
    d_widths = [10, 6, 42, 18, 16, 14, 70, 50]
    for i, w in enumerate(d_widths, 1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    ws2.freeze_panes = "A2"
    ws2.auto_filter.ref = ws2.dimensions

    wb.save(str(path))


def send_email(subject, body):
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("NOTIFY_TO")
    if not host or not to:
        print("   (e-mail not sent: set SMTP_HOST and NOTIFY_TO environment variables)")
        return
    user, pwd = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASS", "")
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, to
    msg["From"] = os.environ.get("NOTIFY_FROM", user or "slbfe-checker@localhost")
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
            s.starttls()
            if user:
                s.login(user, pwd)
            s.send_message(msg)
        print(f"   E-mail alert sent to {to}")
    except Exception as e:
        print(f"   E-mail failed: {e}")


# =========================================================================== login
def inquiry_form_present(page) -> bool:
    try:
        return page.locator('input[name="reader"]').count() > 0
    except Exception:
        return False


def _login_screen_visible(pg) -> bool:
    """True while the browser is still on an SLBFE login page:
    the 3-method page (has the Access link) or the agency Sign In form."""
    try:
        if "services.slbfe.lk/account" not in (pg.url or "").lower():
            return False
        return pg.locator(AUTH_ACCESS).count() > 0 or pg.locator(LOGIN_USER).count() > 0
    except Exception:
        return False


def _any_page_has_wrongway(context) -> bool:
    for pg in context.pages:
        try:
            if "wrongway" in (pg.url or "").lower():
                return True
        except Exception:
            pass
    return False


def _verify_session(page, context) -> bool:
    """Probe the target page. This works no matter which tab/window completed
    the login, because the ASP session cookie is shared browser-context-wide."""
    try:
        page.goto(INQUIRY_URL, wait_until="domcontentloaded", timeout=30000)
        if inquiry_form_present(page):
            context.storage_state(path=str(SESSION_FILE))
            return True
    except Exception:
        pass
    return False


def _submit_login_form(page, user: str, password: str):
    """Open the 3-method login page, click 'Authorized Personnel Only -> Access',
    fill the Sign In form (posts to applications.slbfe.lk/chkuser.asp), and
    verify the resulting session against the target page."""
    context = page.context
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector(AUTH_ACCESS, timeout=30000)
    page.click(AUTH_ACCESS)
    page.wait_for_selector(LOGIN_USER, timeout=30000)
    page.fill(LOGIN_USER, user)
    page.fill(LOGIN_PASS, password)
    try:
        page.check("#checkRememberPassword")
    except Exception:
        pass
    page.click(LOGIN_SUBMIT)

    # wait until we leave the Sign In form or a wrongway rejection appears
    deadline = time.time() + 30
    while time.time() < deadline:
        if ("wrongway" in (page.url or "").lower()
                or "services.slbfe.lk/account" not in (page.url or "").lower()
                or _any_page_has_wrongway(context)):
            break
        page.wait_for_timeout(500)

    if "wrongway" in (page.url or "").lower() or _any_page_has_wrongway(context):
        try:
            msg = re.sub(r"\s+", " ", page.inner_text("body")).strip()
        except Exception:
            msg = "Wrong User Name or Password"
        return False, msg[:140]

    if _verify_session(page, context):
        return True, ""
    return False, "Login did not produce a valid portal session."


def _prompt_credentials():
    user = input("SLBFE username: ").strip()
    password = getpass.getpass("SLBFE password: ")
    return user, password


def _wait_manual_browser_login(page) -> None:
    """Headed login: the browser shows the 3-method SLBFE page and the user
    signs in manually. The script polls the shared browser session (works even
    if the portal opens in a new tab) and continues automatically."""
    context = page.context
    popups = []
    context.on("page", lambda p: popups.append(p))

    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    print("\n" + "=" * 70)
    print("LOGIN REQUIRED - the browser now shows the SLBFE page with 3 login methods.")
    print("1. Click 'Access' under 'Authorized Personnel Only' (for agencies).")
    print("2. Enter your username and password on the Sign In page.")
    print("(Credentials are typed into the website, not this terminal.)")
    print("The script continues automatically after a successful login.")
    print("=" * 70)

    deadline = time.time() + 300
    last_note = 0.0
    while time.time() < deadline:
        active = popups[-1] if popups and not popups[-1].is_closed() else page
        try:
            active.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass

        if "wrongway" in (active.url or "").lower() or _any_page_has_wrongway(context):
            print("[login] sign-in was rejected - returning to the 3-method login page...")
            try:
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            time.sleep(1)
            continue

        if _login_screen_visible(active):
            if time.time() - last_note > 15:
                print("[login] waiting for you to complete the sign-in in the browser...")
                last_note = time.time()
            time.sleep(2)
            continue

        # left the login pages -> check whether the session cookie works
        print("[login] sign-in detected - verifying the portal session...")
        if _verify_session(page, context):
            for p in popups:   # close any helper tabs, keep the working page
                try:
                    if not p.is_closed():
                        p.close()
                except Exception:
                    pass
            print("[login] login OK - session saved. Starting automation...")
            return
        time.sleep(2)

    raise SystemExit("Login was not completed within 5 minutes. Re-run the script.")


def ensure_login(page, headless: bool) -> None:
    """1) reuse saved session, 2) SLBFE_USER/SLBFE_PASS env vars,
    3) in-browser login (headed) or terminal prompt (headless)."""
    page.goto(INQUIRY_URL, wait_until="domcontentloaded", timeout=30000)
    if inquiry_form_present(page):
        page.context.storage_state(path=str(SESSION_FILE))
        print("[login] saved session is valid")
        return

    user, password = os.getenv(USER_ENV), os.getenv(PASS_ENV)
    if user and password:
        ok, msg = _submit_login_form(page, user, password)
        if ok:
            print("[login] automatic login OK (environment credentials)")
            return
        print(f"[login] environment credentials rejected: {msg or 'unknown error'}")

    if not headless:
        _wait_manual_browser_login(page)
        return

    print("[login] no valid session and no SLBFE_USER/SLBFE_PASS environment variables.")
    for attempt in range(3):
        try:
            user, password = _prompt_credentials()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            break
        ok, msg = _submit_login_form(page, user, password)
        if ok:
            print("[login] login OK - session saved")
            return
        print(f"[login] rejected ({msg or 'unknown error'}); {2 - attempt} attempt(s) left.")
    raise SystemExit(
        "Login failed. Set SLBFE_USER and SLBFE_PASS environment variables, "
        "or run once without --headless to sign in inside the browser."
    )


# =========================================================================== main
def main():
    ap = argparse.ArgumentParser(description="SLBFE Pre Departure Training checker")
    ap.add_argument("--input", default=str(SCRIPT_DIR / "name.txt"))
    ap.add_argument("--outdir", default=str(SCRIPT_DIR))
    ap.add_argument("--only", help="Row numbers, e.g. 1-10,17")
    ap.add_argument("--recheck-all", action="store_true", help="Ignore progress.json and check everyone again")
    ap.add_argument("--notify-email", action="store_true", help="E-mail the not-trained list at the end")
    ap.add_argument("--delay", type=float, default=1.0, help="Seconds between searches")
    ap.add_argument("--timeout", type=int, default=30, help="Page timeout (seconds)")
    ap.add_argument("--headless", action="store_true", help="Not recommended: you must log in manually")
    args = ap.parse_args()

    out = Path(args.outdir)
    shot_dir = out / "screenshots"
    debug_dir = out / "debug"
    shot_dir.mkdir(parents=True, exist_ok=True)
    progress_path = out / "progress.json"
    excel_path = out / EXCEL_FILE

    if not Path(args.input).exists():
        sys.exit(f"Input file not found: {args.input}")
    people = parse_people(args.input)
    only = parse_only(args.only)
    if only:
        people = [p for p in people if p["no"] in only]
    if not people:
        sys.exit("No people found - check the input file / --only value.")

    progress = load_json(progress_path, {})
    todo = [p for p in people
            if args.recheck_all or progress.get(person_key(p), {}).get("status") != "TRAINED"]
    already = len(people) - len(todo)
    print(f"{len(people)} applicants loaded | {already} already TRAINED (skipped) | {len(todo)} to check")

    timeout_ms = args.timeout * 1000
    search_log = []
    if todo:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=args.headless)
            ctx_kw = {"storage_state": str(SESSION_FILE)} if SESSION_FILE.exists() else {}
            context = browser.new_context(viewport={"width": 1280, "height": 800}, **ctx_kw)
            page = context.new_page()

            alerts = []
            page.on("dialog", lambda d: (alerts.append(d.message), d.accept()))

            # opens the SLBFE Sign In page automatically when no session exists
            ensure_login(page, args.headless)

            try:
                for n, p in enumerate(todo, 1):
                    print(f"\n[{n}/{len(todo)}] No.{p['no']}  {p['name']}")
                    known = {p[k] for _, k in ID_ORDER if p[k]}
                    entry = {
                        "no": p["no"], "name": p["name"], "status": "", "found_by": "", "training": "",
                        "training_date": "", "screenshots": "", "blacklisted": "", "ids_checked": "",
                        "note": "", "last_checked": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    checked, any_records, shots = [], [], []
                    not_found_count, any_error, review_note = 0, False, ""
                    seen = set()

                    for label, key in ID_ORDER:
                        ident = p[key]
                        if not ident or ident in seen:
                            continue
                        seen.add(ident)
                        drow = {"time": datetime.now().strftime("%H:%M:%S"), "no": p["no"], "name": p["name"],
                                "id_type": label, "id_searched": ident, "status": "", "records": "", "note": ""}

                        if not site_accepts(ident):
                            drow.update(status="INVALID_FORMAT", note="Rejected by the site's own format check")
                            search_log.append(drow)
                            checked.append(f"{label}:INVALID_FORMAT")
                            print(f"   {label:<18} {ident:<14} -> INVALID_FORMAT")
                            continue

                        try:
                            res = lookup(page, ident, alerts, timeout_ms)
                            if res["status"] == "SESSION_EXPIRED":
                                print("   !! Session expired - automatic re-login required.")
                                ensure_login(page, args.headless)
                                res = lookup(page, ident, alerts, timeout_ms)
                        except Exception as e:
                            res = {"status": "ERROR", "records": [], "blacklisted": "", "note": str(e)[:200]}

                        if res["status"] == "UNKNOWN_PAGE":
                            debug_dir.mkdir(parents=True, exist_ok=True)
                            stem = debug_dir / f"debug_{p['no']}_{ident}"
                            page.screenshot(path=str(stem) + ".png", full_page=True)
                            Path(str(stem) + ".html").write_text(page.content(), encoding="utf-8")

                        drow.update(status=res["status"], records=" || ".join(res["records"]), note=res["note"])
                        search_log.append(drow)
                        checked.append(f"{label}:{res['status']}")
                        if res["blacklisted"]:
                            entry["blacklisted"] = res["blacklisted"]
                            print(f"   !! BLACKLIST FLAG: {res['blacklisted']}")
                        if res["status"] in ("ERROR", "NO_RESPONSE", "SESSION_EXPIRED", "UNKNOWN_PAGE"):
                            any_error = True
                            entry["note"] = res["note"]

                        # yellow "Not Found !!!" page -> screenshot it as proof for THIS number
                        if res["status"] == "NOT_FOUND":
                            not_found_count += 1
                            shot = (shot_dir / f"{p['no']:03d}_{safe_filename(p['name'])}_"
                                             f"{label.replace(' ', '')}_{ident}.png")
                            page.screenshot(path=str(shot), full_page=True)
                            shots.append(shot.name)
                            print(f"   {label:<18} {ident:<14} -> NOT FOUND (no record under this number) "
                                  f"screenshot: {shot.name}")
                            time.sleep(args.delay)
                            continue

                        if res["status"] != "FOUND":
                            print(f"   {label:<18} {ident:<14} -> {res['status']} {res['note']}")
                            time.sleep(args.delay)
                            continue

                        any_records.extend(res["records"])
                        for r in res["records"]:
                            print(f"   {label:<18} {ident:<14} -> {r}")

                        hit = find_trained_record(res["records"], known)
                        if hit and hit[2]:  # Pre departure Orientation + same person
                            idx, rec, _ = hit
                            shot = shot_dir / f"{p['no']:03d}_{safe_filename(p['name'])}_TRAINED.png"
                            take_proof_screenshot(page, idx, shot)
                            shots.append(shot.name)
                            entry.update(status="TRAINED", found_by=f"{label}={ident}",
                                         training=rec["text"], training_date=rec["date"],
                                         screenshots="; ".join(shots))
                            print(f"   >>> PRE DEPARTURE ORIENTATION CONFIRMED ({rec['date']}) "
                                  f"- screenshot saved: {shot.name}")
                            break
                        if hit and not hit[2]:
                            review_note = (f"Pre departure Orientation record found under {label} {ident} but its "
                                           f"PP/NIC ({hit[1]['pp']}/{hit[1]['nic']}) are not this applicant's numbers")
                            print(f"   !! {review_note}")
                        time.sleep(args.delay)

                    entry["ids_checked"] = ", ".join(checked)
                    entry["screenshots"] = "; ".join(shots)
                    if entry["status"] != "TRAINED":
                        if review_note:
                            entry.update(status="REVIEW", note=review_note)
                        elif any_error:
                            entry["status"] = "INCOMPLETE"
                            entry["note"] = (entry["note"] or "Some searches failed") + " - re-run to retry"
                        elif not any_records and not_found_count:
                            entry["status"] = "NOT_FOUND"
                            entry["note"] = ("No record found under any checked number "
                                             f"({not_found_count} Not Found page(s); see screenshots folder)")
                        elif any_records:
                            kinds = sorted({parse_record(r)["kind"] or "?" for r in any_records})
                            entry["status"] = "NOT_TRAINED"
                            entry["note"] = ("Records exist but NO Pre departure Orientation course. "
                                             "Found only: " + ", ".join(kinds))
                        else:
                            entry["status"] = "NOT_TRAINED"
                            entry["note"] = "No usable identifier produced a record"
                        print(f"   *** {entry['status']}: {entry['note']}")

                    progress[person_key(p)] = entry
                    save_json(progress_path, progress)  # saved after EVERY applicant
            except KeyboardInterrupt:
                print("\nInterrupted - progress has been saved. Run again to continue.")
            finally:
                browser.close()

    # ------------------------------------------------------------------ report
    rows = [progress[person_key(p)] for p in people if person_key(p) in progress]
    for p in people:
        if person_key(p) not in progress:
            rows.append({"no": p["no"], "name": p["name"], "status": "NOT_CHECKED"})
    rows.sort(key=lambda r: int(r["no"]))
    write_excel(excel_path, rows, search_log)

    pending = [r for r in rows if r["status"] != "TRAINED"]
    n_shots = len(list(shot_dir.glob("*.png")))

    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    lines = [f"SLBFE Pre Departure Training check - {datetime.now():%Y-%m-%d %H:%M}",
             f"Applicants without a confirmed Pre departure Orientation: {len(pending)}", ""]
    for r in pending:
        lines.append(f"No.{r['no']:>3}  {r['name']}  [{r['status']}]  {r.get('note', '')}")
    alert_text = "\n".join(lines)

    print("\n================ SUMMARY ================")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"{v:>4}  {k}")
    print(f"\nExcel report : {excel_path.name} (sheets: Applicants, Search Log)")
    print(f"Screenshots  : {n_shots} file(s) in the screenshots/ folder")
    print(f"Run memory   : progress.json (TRAINED applicants are skipped next run)")
    if pending:
        print("\n" + "!" * 60)
        print(f"ACTION NEEDED - {len(pending)} applicant(s) have NO confirmed Pre departure Orientation:")
        for r in pending:
            print(f"   No.{r['no']:>3}  {r['name']}  [{r['status']}]")
        print("!" * 60)
        if args.notify_email:
            send_email(f"[SLBFE] {len(pending)} applicants without Pre departure Orientation", alert_text)


if __name__ == "__main__":
    main()
