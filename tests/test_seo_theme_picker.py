from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEO_BASE = ROOT / "src" / "classcatalog" / "templates" / "seo" / "base.html"
SEO_THEME_JS = ROOT / "src" / "classcatalog" / "static" / "seo-theme-picker.js"
MAIN_APP_JS = ROOT / "src" / "classcatalog" / "static" / "app.js"


def test_seo_pages_include_normal_theme_picker_control() -> None:
    html = SEO_BASE.read_text(encoding="utf-8")

    assert 'id="theme-picker" class="theme-picker"' in html
    assert 'id="theme-picker-toggle" class="theme-picker-toggle"' in html
    assert 'id="theme-picker-panel" class="theme-picker-panel" hidden' in html
    assert 'class="theme-toggle-swatches"' in html
    assert 'class="theme-options" role="radiogroup"' in html
    assert '/static/seo-theme-picker.js?v=1' in html


def test_seo_theme_picker_uses_existing_cookie_and_interaction_model() -> None:
    javascript = SEO_THEME_JS.read_text(encoding="utf-8")

    assert 'const themeCookieName = "classcatalog_theme";' in javascript
    assert 'document.documentElement' in javascript
    assert 'button.dataset.themeOption' in javascript
    assert 'button.addEventListener("pointerenter"' in javascript
    assert 'event.key !== "Escape"' in javascript
    assert 'picker.contains(event.target)' in javascript
    assert 'window.localStorage.removeItem("classcatalog_theme_v1")' in javascript


def test_seo_theme_picker_keeps_core_theme_ids_in_sync_with_main_app() -> None:
    seo_javascript = SEO_THEME_JS.read_text(encoding="utf-8")
    main_javascript = MAIN_APP_JS.read_text(encoding="utf-8")

    for theme_id in ("sdsu", "sdsu-dark", "8008", "9009", "dracula", "neon-sunset"):
        assert theme_id in seo_javascript
        assert theme_id in main_javascript


def test_seo_theme_picker_pins_sdsu_themes_before_numeric_theme_ids() -> None:
    javascript = SEO_THEME_JS.read_text(encoding="utf-8")

    assert 'const pinnedThemeIds = ["sdsu", "sdsu-dark"];' in javascript
    assert "...pinnedThemeIds" in javascript
    assert "Object.keys(themeNames).filter" in javascript
    assert "orderedThemeIds.forEach" in javascript
    assert "Object.entries(themeNames).forEach" not in javascript
