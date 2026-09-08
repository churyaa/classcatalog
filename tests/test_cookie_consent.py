from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "classcatalog" / "static"


def test_optional_analytics_is_gated_by_cookie_consent() -> None:
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    privacy = (STATIC / "privacy.html").read_text(encoding="utf-8")
    terms = (STATIC / "terms.html").read_text(encoding="utf-8")
    javascript = (STATIC / "cookie-consent.js").read_text(encoding="utf-8")
    css = (STATIC / "cookie-consent.css").read_text(encoding="utf-8")

    pages = (index, privacy, terms)

    measurement_ids = []
    for html in pages:
        match = re.search(
            r'<meta name="classcatalog-google-analytics-id" content="(G-[A-Za-z0-9_-]+)">',
            html,
        )
        assert match is not None
        measurement_ids.append(match.group(1))

        assert '/static/cookie-consent.css?v=1' in html
        assert '/static/cookie-consent.js?v=1' in html

        # No page may eagerly load Google Analytics before consent.
        assert "https://www.googletagmanager.com/gtag/js?id=" not in html

    assert len(set(measurement_ids)) == 1

    assert 'const CONSENT_KEY = "classcatalog_analytics_consent_v1";' in javascript
    assert '"granted"' in javascript
    assert '"denied"' in javascript
    assert "enableAnalytics()" in javascript
    assert "disableAnalytics()" in javascript
    assert 'document.createElement("script")' in javascript
    assert "googletagmanager.com/gtag/js?id=" in javascript
    assert 'banner.id = "cookie-consent-banner"' in javascript
    assert "Reject analytics" in javascript
    assert "Accept analytics" in javascript
    assert "Cookie settings" in javascript
    assert 'href="/privacy"' in javascript
    assert "expireAnalyticsCookies()" in javascript
    assert "ga-disable-" in javascript

    assert ".cookie-consent-banner" in css
    assert ".cookie-consent-actions" in css
    assert ".footer-cookie-settings" in css
    assert ":focus-visible" in css

    assert "Google Analytics is not loaded until you choose" in privacy
    assert "Accept analytics" in privacy
    assert "Cookie settings" in privacy
