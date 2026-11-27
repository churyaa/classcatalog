"""Regression check: private refresh telemetry does not clutter public search results."""
from pathlib import Path
import re


def test_public_search_count_remains_and_technical_refresh_banner_hidden():
    path = Path(__file__).resolve().parents[0] / 'src' / 'classcatalog' / 'static' / 'index.html'
    # This file is copied to tests/test_public_refresh_status_hidden.py in the repo.
    if not path.is_file():
        path = Path(__file__).resolve().parents[1] / 'src' / 'classcatalog' / 'static' / 'index.html'
    html = path.read_text(encoding='utf-8')
    assert 'id="result-count"' in html
    assert 'id="result-context"' in html
    banners = re.findall(r'<small\s+[^>]*id="seat-freshness"[^>]*>.*?</small>', html, re.DOTALL)
    assert len(banners) == 1
    assert re.search(r'\bhidden(?:\s|=|>)', banners[0])
    assert 'aria-hidden="true"' in banners[0]
    assert 'Seat refresh starting...' not in banners[0]
