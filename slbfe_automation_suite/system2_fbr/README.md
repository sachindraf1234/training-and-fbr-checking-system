# System 2 — Female FBR (Family Background Report) Verification

Standalone, independent Playwright automation for the SLBFE page
**Checking common programmes → Family Background Report Verification**
(`fbr_sms.asp` → posts to `fbr_sms1.asp`).

> **Female applicants only.** The script enforces this itself: rows whose name
> carries a known Sinhala/Tamil **male** given-name token are skipped, rows with
> a **female** token are checked, and unrecognised names are skipped and listed
> as `UNCERTAIN` at startup (add a `Gender`/`Sex` column with `F` to force-include
> them). An explicit `Gender` column always overrides the name heuristic, and
> `#` comment lines are ignored. The skipped names are printed before the
> browser opens. This system shares nothing with System 1 — it even keeps its
> own session file (`slbfe_session_fbr.json`).

## What it does

For every female applicant it searches the FBR page by **current passport**,
then by **current NIC**, and reads the result table:

| Column on the portal | Used for |
| --- | --- |
| Date (SMS Received) | + 6 calendar months = FBR expiry date |
| Passport No / NIC No | identity cross-check against the applicant's **current** numbers (mismatch → `MISMATCH`/`CHECK`) |
| Requested Agency LNO | recorded |
| Status (e.g. Recommended, Pending) | recorded |

Classification per applicant:

- `VALID` — more than 14 days until expiry
- `EXPIRES SOON` — 14 days or fewer
- `EXPIRED` — the 6-month validity has passed
- `NOT FOUND` — no FBR row under either identifier
- `MISMATCH` — a row came back but its PP/NIC are not the current ones
- `ERROR` — technical/session failure (the run continues with the next person)

Every run re-checks everyone from scratch (expiry is recalculated against
today). An evidence screenshot is saved in `output/fbr_screenshots/` whenever a
row is found.

## Setup

```bash
pip install -r requirements.txt          # playwright + openpyxl
playwright install chromium
python -m playwright install-deps chromium   # Linux only, if browser libs are missing
```

## Run

```bash
python fbr_checker.py                    # 3-method login page opens every run
python fbr_checker.py --reuse-session    # skip login when a saved session still works
python fbr_checker.py --headless         # needs SLBFE_USER/SLBFE_PASS (or existing session)
python fbr_checker.py --only 1-10        # row numbers support ranges/lists
python fbr_checker.py --names other.txt --out output
```

The exact login URL `https://services.slbfe.lk/account/ext_account_main`
opens **on every run by default** - it is used unchanged and shows all
**3 login methods** (Public Users / Authorized Personnel Only / Development
Officer). You click **Authorized Personnel Only → Access**, type your
credentials, and the script detects success on its own. It then walks the
portal the way a user does — welcome page → the **Family Background Report
Verification** menu link → the `fbr_sms.asp` search form (a direct-link
fallback is used only if the menu cannot be found). Other authentication
options: `SLBFE_USER`/`SLBFE_PASS` environment variables perform the whole
login automatically (also under `--headless`), and `--reuse-session` reuses
`slbfe_session_fbr.json` without showing the login page. If the session
expires mid-run the cookies are cleared, a full re-login is performed and the
applicant is retried (up to 2 times) before being recorded as `ERROR`.

## Outputs (all inside this system's `output/`)

- `fbr_report_<timestamp>.xlsx` — timestamped run, colour coded (green VALID,
  amber EXPIRES SOON, red EXPIRED, grey other) with five sheets:
  **All Females**, **VALID FBR**, **EXPIRED & EXPIRING**, **NO FBR**,
  **REVIEW & ERRORS** — every female appears in exactly one group sheet
- `fbr_report_<timestamp>.csv` — same data in one flat table
- `fbr_report_latest.xlsx` / `.csv` — always-newest copies for quick access
- `fbr_summary.json` — machine-readable summary for the suite dashboard,
  including the skipped non-female and uncertain name lists
- `fbr_screenshots/NNN_NAME.png` — evidence per found record
- The terminal ends with named lists: **GOT VALID FBR**, **EXPIRES SOON**,
  **FBR EXPIRED**, **NO FBR RECORD**, **REVIEW / ERRORS**

## Self-test (no SLBFE login)

```bash
python tests/test_fbr_flow.py
```

Starts a local mock of the welcome/menu page plus `fbr_sms.asp`/`fbr_sms1.asp`
and covers female-only filtering (name tokens, Gender column, uncertain
names), the portal-menu walk, VALID / EXPIRES SOON / EXPIRED (the exact
`26/01/2026` screenshot case), NOT FOUND, MISMATCH, short-passport alert →
NIC fallback, session-expiry → re-login retries, screenshots and the
multi-sheet Excel / CSV / JSON outputs.
