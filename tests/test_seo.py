from __future__ import annotations

from pathlib import Path
import json
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from classcatalog.main import app


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "classcatalog" / "static"


def test_root_robots_and_sitemap_are_public() -> None:
    with TestClient(app) as client:
        robots = client.get("/robots.txt")
        sitemap = client.get("/sitemap.xml")

    assert robots.status_code == 200
    assert sitemap.status_code == 200

    assert robots.headers["content-type"].startswith("text/plain")
    assert "application/xml" in sitemap.headers["content-type"]

    assert "User-agent: *" in robots.text
    assert "Allow: /" in robots.text
    assert "Sitemap: https://classcatalog.cc/sitemap.xml" in robots.text

    root = ET.fromstring(sitemap.text)
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [node.text for node in root.findall("sm:url/sm:loc", namespace)]

    assert urls == [
        "https://classcatalog.cc/",
        "https://classcatalog.cc/privacy",
        "https://classcatalog.cc/terms",
    ]


def test_homepage_has_search_metadata_and_structured_data() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert "<title>SDSU Class Search & Course Finder | ClassCatalog</title>" in html
    assert (
        'name="description" content="Search SDSU classes by subject, professor, '
        'schedule, seats, instruction mode, ratings, and degree requirements '
        'with ClassCatalog."'
    ) in html

    assert '<link rel="canonical" href="https://classcatalog.cc/">' in html
    assert '<meta name="robots" content="index,follow">' in html
    assert '<meta property="og:site_name" content="ClassCatalog">' in html
    assert '<meta property="og:type" content="website">' in html
    assert '<meta property="og:url" content="https://classcatalog.cc/">' in html
    assert '<meta name="twitter:card" content="summary">' in html

    marker = (
        '<script type="application/ld+json" '
        'id="classcatalog-website-schema">'
    )
    start = html.index(marker) + len(marker)
    end = html.index("</script>", start)
    schema = json.loads(html[start:end])

    assert schema["@context"] == "https://schema.org"
    assert schema["@type"] == "WebSite"
    assert schema["name"] == "ClassCatalog"
    assert schema["url"] == "https://classcatalog.cc/"

    assert 'name="keywords"' not in html.lower()


def test_legal_pages_have_canonicals_and_social_metadata() -> None:
    privacy = (STATIC / "privacy.html").read_text(encoding="utf-8")
    terms = (STATIC / "terms.html").read_text(encoding="utf-8")

    assert '<link rel="canonical" href="https://classcatalog.cc/privacy">' in privacy
    assert '<meta property="og:url" content="https://classcatalog.cc/privacy">' in privacy
    assert "Read the ClassCatalog Privacy Policy" in privacy

    assert '<link rel="canonical" href="https://classcatalog.cc/terms">' in terms
    assert '<meta property="og:url" content="https://classcatalog.cc/terms">' in terms
    assert "Read the ClassCatalog Terms of Service" in terms
