# SLBFE Automation Suite

Two **separate, independent** Playwright automation systems under one common
dashboard. The systems never share code, input files, browser sessions or
output folders — the dashboard only launches them and displays their results.

| | System 1 | System 2 |
| --- | --- | --- |
| Name | Pre Departure Training check | Family Background Report (FBR) check |
| Portal page | View - Training / Experience / Blacklist (`TraRegChk.asp`) | Checking common programmes → FBR Verification (`fbr_sms.asp`) |
| Applies to | **All applicants** | **Females only** |
| Folder | `system1_training/` | `system2_fbr/` |
| Input | `system1_training/name.txt` | `system2_fbr/name_female.txt` (or a table with a Gender column) |
| Main script | `slbfe_training_checker.py` | `fbr_checker.py` |
| Session file | `slbfe_session.json` (auto-saved) | `slbfe_session_fbr.json` (its own file; fresh login every run unless `--reuse-session`) |
| Key outputs | `training_results.xlsx` (Applicants + Search Log sheets), `screenshots/`, `progress.json` | `output/fbr_report_latest.xlsx/.csv`, `fbr_summary.json` |
| Retries | skips already-TRAINED applicants via `progress.json` | always full re-check (6-month expiry is recalculated daily) |

## Folder layout

```
slbfe_automation_suite/
├── run_dashboard.py            # common launcher + combined status / HTML dashboard
├── combined_dashboard.html     # generated on demand (self-contained, no internet needed)
├── system1_training/           # SYSTEM 1 - independent
│   ├── slbfe_training_checker.py
│   ├── name.txt
│   ├── requirements.txt
│   ├── tests/
│   └── legacy_v1/              # archived first version (reference only)
└── system2_fbr/                # SYSTEM 2 - independent
    ├── fbr_checker.py
    ├── name_female.txt         # FEMALE applicants only
    ├── requirements.txt
    ├── output/
    └── tests/
```

## One-time setup

```bash
# from each system folder (or once globally):
pip install playwright openpyxl
playwright install chromium
python -m playwright install-deps chromium     # Linux only, if browser libs are missing
```

## Using the common dashboard

Interactive menu:

```bash
python run_dashboard.py
```

```text
 1. Run SYSTEM 1 - Pre Departure Training check (all applicants)
 2. Run SYSTEM 2 - Female FBR check (female only)
 3. Combined status overview (terminal)
 4. Build combined_dashboard.html
 5. Run both self-test suites
 0. Exit
```

Each "Run" launches the chosen script as its **own process** with the working
directory set to that system's folder, so both systems stay fully isolated.
Any flags you type are passed straight through to that system's script.

Scripted / scheduled use:

```bash
python run_dashboard.py --system 1                      # run System 1
python run_dashboard.py --system 2 -- --headless       # run System 2, flags after --
python run_dashboard.py --overview                      # combined terminal status
python run_dashboard.py --html                          # refresh combined_dashboard.html
python run_dashboard.py --test                          # both mock-based self-tests
```

## Running a system directly (without the dashboard)

```bash
python system1_training/slbfe_training_checker.py --limit 3
python system2_fbr/fbr_checker.py --only 1-5
```

## Login / sessions

Both systems use the same SLBFE agency login at the **exact, unmodified**
URL `https://services.slbfe.lk/account/ext_account_main`, which displays all
**3 login methods** (Public Users / Authorized Personnel Only / Development
Officer). The scripts never deep-link past this page: when credentials are
entered automatically they click the **Authorized Personnel Only → Access**
link themselves (Sign In fields `usrLoginName` / `usrPassWord` posting to
`applications.slbfe.lk/chkuser.asp`); in manual mode you click it. The two
systems keep **separate browser contexts and separate session files**
(`slbfe_session.json` vs `slbfe_session_fbr.json`) — one's login never
silently satisfies the other. Authentication options:

1. type the credentials directly into the visible browser window — System 2
   opens the 3-method page on every run; System 1 reuses its saved session and
   only shows the page when needed (System 2 can do the same with
   `--reuse-session`)
2. `SLBFE_USER` / `SLBFE_PASS` environment variables — fully automatic, also in
   `--headless` mode
3. headless without env vars prompts in the terminal (3 attempts)

After login System 2 walks the portal menu (welcome page → Family Background
Report Verification) before starting. Wrong credentials are detected from the
`wrongway.asp` redirect and reported with the server's message. If a session
expires mid-run the cookies are cleared, a full re-login is performed and the
current applicant is retried before being recorded as an error.

## Combined HTML dashboard

`python run_dashboard.py --html` writes a self-contained
`combined_dashboard.html` (inline CSS, no external resources) with a status
card and full detail table per system, plus relative links to each system's
report files. It only reads the systems' JSON outputs — no data is merged or
copied between systems.
