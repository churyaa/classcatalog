from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from classcatalog.main import app


STATIC = Path(__file__).resolve().parents[1] / "src" / "classcatalog" / "static"


def test_simple_about_privacy_and_terms_pages() -> None:
    with TestClient(app) as client:
        home = client.get("/")
        privacy = client.get("/privacy")
        terms = client.get("/terms")

    assert home.status_code == 200
    assert privacy.status_code == 200
    assert terms.status_code == 200

    assert 'id="about-page"' in home.text
    assert 'class="legal-document"' in home.text
    assert 'href="/privacy">Privacy</a>' in home.text
    assert 'href="/terms">Terms</a>' in home.text

    about_slice = home.text[
        home.text.index('id="about-page"'):home.text.index('id="admin-page"')
    ]
    assert "about-card" not in about_slice
    assert "about-icon" not in about_slice

    assert "<title>Privacy Policy — ClassCatalog</title>" in privacy.text
    assert 'id="privacy-page-title">Privacy Policy</h2>' in privacy.text
    assert "Google Analytics" in privacy.text
    assert "about-card" not in privacy.text
    assert "about-icon" not in privacy.text

    assert "<title>Terms of Service — ClassCatalog</title>" in terms.text
    assert 'id="terms-page-title">Terms of Service</h2>' in terms.text
    assert "ClassCatalog is a planning aid" in terms.text
    assert "about-card" not in terms.text
    assert "about-icon" not in terms.text

    legal_css = (STATIC / "legal.css").read_text(encoding="utf-8")
    assert ".legal-document" in legal_css
    assert ".legal-page-shell" in legal_css
