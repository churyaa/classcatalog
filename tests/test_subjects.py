from __future__ import annotations

from classcatalog.subjects import (
    SUBJECT_ABBREVIATIONS,
    encoded_subject,
    normalize_subject_input,
)


def test_subject_seed_has_127_unique_values() -> None:
    assert len(SUBJECT_ABBREVIATIONS) == 127
    assert len(set(SUBJECT_ABBREVIATIONS)) == 127
    assert "SEG" in SUBJECT_ABBREVIATIONS
    assert "SEGS" not in SUBJECT_ABBREVIATIONS


def test_subjects_with_spaces_are_encoded() -> None:
    assert encoded_subject("CIV E") == "CIV+E"


def test_legacy_segs_input_maps_to_current_seg_subject() -> None:
    assert normalize_subject_input("SEGS") == "SEG"
