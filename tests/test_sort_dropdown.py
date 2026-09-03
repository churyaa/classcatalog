from __future__ import annotations

from pathlib import Path


def test_sort_dropdown_orders_ascending_before_descending() -> None:
    html_path = (
        Path(__file__).parents[1]
        / "src"
        / "classcatalog"
        / "static"
        / "index.html"
    )
    html = html_path.read_text(encoding="utf-8")
    expected_values = (
        "alphabetical",
        "alphabetical_desc",
        "professor_rating_asc",
        "professor_rating",
        "professor_difficulty_asc",
        "professor_difficulty",
        "reviews_asc",
        "reviews",
        "take_again_asc",
        "take_again",
    )
    positions = [html.index(f'value="{value}"') for value in expected_values]
    assert positions == sorted(positions)


def test_sort_dropdown_keeps_only_professor_difficulty_labels() -> None:
    html_path = (
        Path(__file__).parents[1]
        / "src"
        / "classcatalog"
        / "static"
        / "index.html"
    )
    html = html_path.read_text(encoding="utf-8")
    assert "Class Difficulty (Low to High)" not in html
    assert "Class Difficulty (High to Low)" not in html
    assert "Professor Difficulty (Low to High)" in html
    assert "Professor Difficulty (High to Low)" in html
