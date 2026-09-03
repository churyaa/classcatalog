from __future__ import annotations

import json
from pathlib import Path

from classcatalog.importers.sdsu_schedule_csv import (
    import_schedule_csv,
    write_sections_json,
)
from classcatalog.models import InstructionMode, SeatStatus, Weekday


CSV_TEXT = """College,Career,Class Nbr,Subject,Catalog Nbr,Title,Course Description,Class Section,Course ID,Component,Associated Class,Component Units,Facility ID,Meeting Start,Meeting End,Standard Meeting Pattern,Last Name,Initials,Class Status,Enrollment Capacity,Enrollment Total,Instruction Mode
SCI,UGRD,3985,ASTR,101,Principles of Astronomy,Survey of astronomy,1,360,LEC,1,3.00,LH345,11:00 AM,11:50 AM,MWF,Sandquist,E,A,80,72,P
SCI,UGRD,6221,BIOL,100,General Biology,Foundations of biology,4,9598,LLC,4,3.00,AL201,02:00 PM,03:15 PM,T,Ekdale,E,A,500,498,HY
SCI,UGRD,6221,BIOL,100,General Biology,Foundations of biology,4,9598,LLC,4,3.00,ONLINE,,,,Ekdale,E,A,500,498,HY
SCI,UGRD,6721,BIOL,100L,General Biology Lab,Experimental biology laboratory,1,9600,LAB,1,1.00,PSFA377,11:00 AM,01:40 PM,M,,,A,26,26,P
SCI,UGRD,6721,BIOL,100L,General Biology Lab,Experimental biology laboratory,1,9600,LAB,1,1.00,PSFA377,11:00 AM,01:40 PM,M,Bhaskaran,N,A,26,26,P
SCI,UGRD,7000,CS,560,Algorithms,Algorithm design and analysis,1,999,LEC,1,3.00,ONLINE,,,,Doe,J,A,40,40,ON
"""


def test_import_schedule_csv_groups_duplicate_rows(tmp_path: Path) -> None:
    path = tmp_path / "fall.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")

    sections, report = import_schedule_csv(path, term="Fall 2026")

    assert report.rows_read == 6
    assert report.sections_created == 4
    assert [section.course_code for section in sections] == [
        "ASTR 101",
        "BIOL 100",
        "BIOL 100L",
        "CS 560",
    ]

    astronomy = sections[0]
    assert astronomy.instruction_mode is InstructionMode.IN_PERSON
    assert astronomy.meetings[0].days == (Weekday.MON, Weekday.WED, Weekday.FRI)
    assert astronomy.description == "Survey of astronomy"
    assert astronomy.seat_capacity == 80
    assert astronomy.seats_enrolled == 72
    assert astronomy.seats_available == 8
    assert astronomy.seat_status is SeatStatus.OPEN

    biology = sections[1]
    assert biology.instruction_mode is InstructionMode.HYBRID
    assert len(biology.meetings) == 1

    lab = sections[2]
    assert len(lab.meetings) == 1
    assert lab.instructor == "Bhaskaran, N"

    online = sections[3]
    assert online.instruction_mode is InstructionMode.ONLINE_ASYNCHRONOUS
    assert online.meetings == ()
    assert online.seat_capacity == 40
    assert online.seats_enrolled == 40
    assert online.seats_available == 0
    assert online.seat_status is SeatStatus.CLOSED


def test_write_sections_json_can_append_terms(tmp_path: Path) -> None:
    path = tmp_path / "fall.csv"
    path.write_text(CSV_TEXT, encoding="utf-8")
    fall, _ = import_schedule_csv(path, term="Fall 2026")
    spring, _ = import_schedule_csv(path, term="Spring 2027")
    output = tmp_path / "sections.json"

    first = write_sections_json(fall, output=output)
    second = write_sections_json(spring, output=output, append=True)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert first.sections_created == 4
    assert second.sections_created == 8
    assert len(payload) == 8
    assert {item["term"] for item in payload} == {"Fall 2026", "Spring 2027"}
