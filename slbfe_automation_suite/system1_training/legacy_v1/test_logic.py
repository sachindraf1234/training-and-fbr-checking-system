"""Offline tests for the parsing / identifier-selection logic (no browser needed)."""
from training_checker import parse_option, candidate_identifiers, load_people


def test_training_record_from_screenshot():
    rec = parse_option(
        "PP N8074311 NIC 980260925V Training~ Romania Pre departure Training 14/10/2026"
    )
    assert rec.kind == "Training"
    assert rec.pp == "N8074311"
    assert rec.nic == "980260925V"
    assert rec.name == "Romania Pre departure Training"
    assert rec.date == "14/10/2026"


def test_training_record_with_spacing():
    rec = parse_option(
        "PP N9852624 NIC 200403813512 Training~ Orientation for other than "
        "Domestic Housekeeping-2 days         31/08/2026"
    )
    assert rec.kind == "Training"
    assert rec.date == "31/08/2026"
    assert rec.name == "Orientation for other than Domestic Housekeeping-2 days"


def test_registration_record():
    rec = parse_option(
        "PP N8074311 NIC 980260925V Registration~ > 27/06/2023 | Qatar | "
        "Specialist Marketing | Source:2860-SKY HIGH SERVI"
    )
    assert rec.kind == "Registration"
    assert rec.date == "27/06/2023"
    assert rec.country == "Qatar"
    assert rec.name == "Specialist Marketing"
    assert rec.source.startswith("Source:2860")


def test_unknown_line_is_other():
    rec = parse_option("some unexpected server text")
    assert rec.kind == "Other"
    assert rec.raw_text == "some unexpected server text"


def test_candidate_order_and_dedup():
    person = {
        "no": "2", "name": "TEST",
        "current_passport": "N11030886", "current_nic": "820453352V",
        "previous_passport": "N5408869", "previous_nic": "-",
    }
    cands = candidate_identifiers(person)
    assert [c[2] for c in cands] == ["N11030886", "820453352V", "N5408869"]
    assert [c[1] for c in cands] == [
        "Current Passport", "Current NIC", "Previous Passport"
    ]

    # duplicate values across columns are tried only once
    person2 = dict(person, previous_passport="N11030886", previous_nic="")
    assert [c[2] for c in candidate_identifiers(person2)] == [
        "N11030886", "820453352V"
    ]

    # no identifiers at all
    person3 = {k: "" for k in
               ("current_passport", "current_nic", "previous_passport", "previous_nic")}
    person3.update(no="9", name="EMPTY")
    assert candidate_identifiers(person3) == []


def test_real_people_csv_loads():
    people = load_people("people.csv")
    assert len(people) == 142
    # row 2 has a previous passport -> 3 candidates
    cands = candidate_identifiers(people[1])
    assert len(cands) == 3
    # every row has at least one usable identifier
    for p in people:
        assert candidate_identifiers(p), f"row {p['no']} has no identifier"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("\nAll logic tests passed.")
