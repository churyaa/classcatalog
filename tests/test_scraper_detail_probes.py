from __future__ import annotations

from classcatalog.scraping.main import CLASS_DETAIL_TAB_PROBES


def test_deep_scraper_skips_textbook_materials_tab() -> None:
    assert CLASS_DETAIL_TAB_PROBES == ("CD", "MI", "CA")
    assert "TI" not in CLASS_DETAIL_TAB_PROBES