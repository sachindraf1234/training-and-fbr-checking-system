# System 1 — Pre Departure Training Checker

Standalone, independent Playwright automation for the SLBFE portal page
**"View - Training / Experience / Blacklist"** (`TraRegChk.asp`).

It checks **all applicants** in `name.txt` (there is no gender filter here —
the female-only FBR requirement belongs to System 2) and proves whether each
person has completed the **Romania Pre departure Orientation Programme** (the
portal's Pre Departure Training course).

## What it does

For each applicant the four identifiers are tried in this order, stopping as
soon as the course is confirmed:

1. Current Passport → 2. Current NIC → 3. Previous Passport → 4. Previous NIC

Blanks, `-` and duplicate values are skipped. A record counts as trained only
when a **Training** row contains "Pre departure Orientation" — i.e. the live
portal text `Training~ Romania Pre departure Orientation Programme <date>`.
Other courses do **not** qualify (e.g. "Orientation for other than Domestic
Housekeeping-2 days", "5 Day Training Programme for Female Cleaners",
"Domestic Housekeeping-Middle East - 25 Days", "2 Days Orientation Programme
(Online)"). The PP/NIC inside the returned record must match the applicant —
mismatches become `REVIEW`.

When the portal answers with its yellow **"Not Found !!! The number you
entered [...] not found"** page, that number has no record at all; the page is
screenshotted as proof and the next identifier is tried.

| Status | Meaning |
| --- | --- |
| `TRAINED` | Romania Pre departure Orientation Programme found; proof screenshot saved |
| `NOT_TRAINED` | records exist but no Pre departure Orientation course |
| `NOT_FOUND` | every searched number returned the yellow Not Found page (proof screenshots saved) |
| `REVIEW` | qualifying course found under PP/NIC belonging to someone else |
| `INCOMPLETE` | some searches failed (timeout/session/unknown page) — re-run to retry |
| `INVALID_FORMAT` | identifier rejected by the site's own format rule |

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
python -m playwright install-deps chromium   # Linux only, if needed
```

## Run

```bash
python slbfe_training_checker.py                 # normal run (TRAINED people skipped)
python slbfe_training_checker.py --only 1-10,17  # row-number ranges/lists
python slbfe_training_checker.py --recheck-all   # ignore progress.json
python slbfe_training_checker.py --notify-email  # optional SMTP alert (see script header)
```

The exact login URL `https://services.slbfe.lk/account/ext_account_main`
opens automatically when no valid session exists - it is used unchanged and
shows all **3 login methods** (Public Users / Authorized Personnel Only /
Development Officer). For automatic credential entry the script clicks the
**Authorized Personnel Only → Access** link to reach the Sign In form
(fields `usrLoginName`/`usrPassWord`). Authentication, in order:
(1) saved `slbfe_session.json`, (2) `SLBFE_USER`/`SLBFE_PASS` environment
variables for fully automatic login (works with `--headless`), (3) type your
credentials directly into the visible browser window — the script detects
success and continues on its own. Re-login happens automatically if the
session expires.

## Outputs (all inside this system's folder)

- `training_results.xlsx` — **one** Excel workbook, two sheets:
  - `Applicants` — one colour-coded row per applicant (status, course found,
    IDs checked, screenshots, notes)
  - `Search Log` — one row per passport/NIC search (full audit trail)
- `screenshots/` — proof images named with row number + applicant name:
  - `NNN_Name_TRAINED.png` — matching record highlighted in the result list
  - `NNN_Name_<IdType>_<number>.png` — each yellow Not Found page
- `progress.json` — state saved after every applicant; `TRAINED` people are
  skipped on the next run, everyone else is re-checked
- `debug/` — raw HTML/PNG dumps only if the portal returns an unknown page

## Self-test (no SLBFE login)

```bash
python tests/test_v2_flow.py
```

Uses a local mock of the ASP pages (including the yellow Not Found page)
covering TRAINED, NOT_TRAINED, NOT_FOUND, REVIEW, INCOMPLETE,
INVALID_FORMAT, blacklist, progress skip, `--only` and session-expiry
handling. The earlier generic v1 implementation is kept under `legacy_v1/`
for reference only.
