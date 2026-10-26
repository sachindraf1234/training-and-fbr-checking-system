"""
parse_people.py
----------------
Converts the markdown table in name.txt (the worker list with Current / Previous
Passport and NIC numbers) into a clean CSV file that training_checker.py can read.

Usage:
    python parse_people.py
    python parse_people.py --input /path/to/name.txt --output people.csv
"""

import argparse
import csv
import re
from pathlib import Path

# Column order expected inside the markdown table
TABLE_HEADERS = [
    "no",
    "name",
    "current_passport",
    "current_nic",
    "previous_passport",
    "previous_nic",
]

CSV_HEADERS = TABLE_HEADERS


def clean_cell(cell: str) -> str:
    """Normalise one table cell: strip markdown bold/italic, dashes, spaces."""
    cell = cell.strip()
    cell = cell.replace("*", "")          # italic/bold markers
    cell = re.sub(r"\\-", "", cell)        # markdown escaped dash  \-
    if cell in {"-", "—", "–", ""}:
        return ""
    return cell.strip().upper()            # passport / NIC are uppercase


def parse_markdown_table(md_text: str):
    rows = []
    for line in md_text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        # split on pipes, drop the empty first/last pieces from the outer pipes
        parts = [p.strip() for p in line.split("|")]
        parts = parts[1:-1] if parts and parts[0] == "" and parts[-1] == "" else parts
        if len(parts) != 6:
            continue
        # skip header row and separator row (:-:, --- etc.)
        joined = "".join(parts)
        if set(joined) <= set(":- "):
            continue
        if parts[0].lower().startswith("no"):
            continue
        values = [clean_cell(p) for p in parts]
        if not values[0].isdigit():
            continue
        rows.append(dict(zip(CSV_HEADERS, values)))
    return rows


def main():
    ap = argparse.ArgumentParser(description="Parse name.txt markdown table into people.csv")
    ap.add_argument("--input", default=str(Path(__file__).parent / "people_source.txt"),
                    help="Path to the markdown table text file")
    ap.add_argument("--output", default=str(Path(__file__).parent / "people.csv"),
                    help="Output CSV path")
    args = ap.parse_args()

    md_text = Path(args.input).read_text(encoding="utf-8")
    people = parse_markdown_table(md_text)

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(people)

    # quick statistics
    with_prev_pp = sum(1 for p in people if p["previous_passport"])
    with_prev_nic = sum(1 for p in people if p["previous_nic"])
    print(f"Parsed {len(people)} people -> {args.output}")
    print(f"  with previous passport : {with_prev_pp}")
    print(f"  with previous NIC      : {with_prev_nic}")


if __name__ == "__main__":
    main()
