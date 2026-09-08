from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "classcatalog" / "static"
SEO_BASE = ROOT / "src" / "classcatalog" / "templates" / "seo" / "base.html"


def test_theme_aware_brand_logo_uses_new_svg_mask() -> None:
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    logo = (STATIC / "classcatalog_cc_logo.svg").read_text(encoding="utf-8")

    assert 'url("/static/classcatalog_cc_logo.svg?v=1")' in css
    assert "background: var(--theme-accent);" in css
    assert 'viewBox="320 230 705 415"' in logo
    assert 'fill="#eb2934"' in logo


def test_theme_aware_favicon_assets_and_pages_are_wired() -> None:
    favicon = (STATIC / "classcatalog_favicon.svg").read_text(encoding="utf-8")
    helper = (STATIC / "theme-favicon.js").read_text(encoding="utf-8")

    assert 'fill="currentColor"' in favicon
    assert 'getPropertyValue("--theme-accent")' in helper
    assert "data:image/svg+xml" in helper
    assert "ClassCatalogThemeFavicon" in helper

    pages = [
        STATIC / "index.html",
        STATIC / "privacy.html",
        STATIC / "terms.html",
        SEO_BASE,
    ]
    for page in pages:
        html = page.read_text(encoding="utf-8")
        assert '/static/classcatalog_favicon.svg?v=1' in html
        assert 'data-theme-favicon' in html
        assert '/static/theme-favicon.js?v=1' in html


def test_favicon_tracks_main_and_seo_theme_preview_changes() -> None:
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    seo_theme_js = (STATIC / "seo-theme-picker.js").read_text(encoding="utf-8")

    assert "window.ClassCatalogThemeFavicon?.sync();" in app_js
    assert "window.ClassCatalogThemeFavicon?.sync();" in seo_theme_js

    # Both pickers route previews and restores through applyTheme, where favicon sync occurs.
    assert "function previewTheme(themeId)" in app_js
    assert "applyTheme(themeId);" in app_js
    assert 'button.addEventListener("pointerenter", () => applyTheme(button.dataset.themeOption));' in seo_theme_js
    assert "function restoreSelectedTheme()" in seo_theme_js
