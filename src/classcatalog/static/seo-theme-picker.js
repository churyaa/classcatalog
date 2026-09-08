(() => {
  const themeCookieName = "classcatalog_theme";
  const themeCookieMaxAgeSeconds = 60 * 60 * 24 * 365;
  const themeNames = {
    sdsu: "SDSU Light",
    "sdsu-dark": "SDSU Dark",
    "after-dark": "After Dark",
    "80s-after-dark": "80s after dark",
    aether: "aether",
    aurora: "aurora",
    "blue-dolphin": "blue dolphin",
    "blueberry-dark": "blueberry dark",
    bushido: "bushido",
    catppuccin: "catppuccin",
    "chaos-theory": "chaos theory",
    cyberspace: "cyberspace",
    dark: "dark",
    "dark-magic-girl": "dark magic girl",
    dots: "dots",
    dracula: "dracula",
    drowning: "drowning",
    "ez-mode": "ez mode",
    fire: "fire",
    fledgling: "fledgling",
    "future-funk": "future funk",
    hammerhead: "hammerhead",
    husqy: "husqy",
    "iceberg-dark": "iceberg dark",
    ishtar: "ishtar",
    joker: "joker",
    laser: "laser",
    luna: "luna",
    mashu: "mashu",
    "matcha-moccha": "matcha moccha",
    matrix: "matrix",
    metaverse: "metaverse",
    "miami-nights": "miami nights",
    mint: "mint",
    monokai: "monokai",
    mountain: "mountain",
    nautilus: "nautilus",
    nebula: "nebula",
    "night-runner": "night runner",
    "our-theme": "our theme",
    "passion-fruit": "passion fruit",
    "red-dragon": "red dragon",
    "red-samurai": "red samurai",
    rgb: "rgb",
    "rose-pine": "rose pine",
    "rose-pine-moon": "rose pine moon",
    ryujinscales: "ryujinscales",
    "sewing-tin": "sewing tin",
    shadow: "shadow",
    "solarized-dark": "solarized dark",
    sonokai: "sonokai",
    superuser: "superuser",
    trance: "trance",
    "tron-orange": "tron orange",
    voc: "voc",
    watermelon: "watermelon",
    wavez: "wavez",
    "8008": "8008",
    "9009": "9009",
    alduin: "alduin",
    "neon-sunset": "Neon Sunset",
  };

  const root = document.documentElement;
  let selectedThemeId = "sdsu";

  function readCookie(name) {
    const encodedName = `${encodeURIComponent(name)}=`;
    const match = document.cookie
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith(encodedName));
    if (!match) return "";
    try {
      return decodeURIComponent(match.slice(encodedName.length));
    } catch (_error) {
      return "";
    }
  }

  function writeCookie(name, value, maxAgeSeconds) {
    let cookie = `${encodeURIComponent(name)}=${encodeURIComponent(value)}; Path=/; Max-Age=${maxAgeSeconds}; SameSite=Lax`;
    if (window.location.protocol === "https:") cookie += "; Secure";
    document.cookie = cookie;
  }

  function normalizeTheme(themeId) {
    return Object.hasOwn(themeNames, themeId) ? themeId : "sdsu";
  }

  function savedTheme() {
    return normalizeTheme(readCookie(themeCookieName) || root.dataset.theme || "sdsu");
  }

  function applyTheme(themeId) {
    root.dataset.theme = normalizeTheme(themeId);
  }

  function persistTheme(themeId) {
    writeCookie(themeCookieName, normalizeTheme(themeId), themeCookieMaxAgeSeconds);
    try {
      window.localStorage.removeItem("classcatalog_theme_v1");
    } catch (_error) {
      // Legacy storage cleanup is optional.
    }
  }

  function syncThemePicker(themeId) {
    const selectedTheme = normalizeTheme(themeId);
    document.querySelectorAll("[data-theme-option]").forEach((button) => {
      button.setAttribute("aria-checked", String(button.dataset.themeOption === selectedTheme));
    });
    const toggle = document.querySelector("#theme-picker-toggle");
    if (toggle) {
      const name = themeNames[selectedTheme];
      toggle.setAttribute("aria-label", `Choose theme. Current theme: ${name}`);
      toggle.title = `Theme: ${name}`;
    }
  }

  function setTheme(themeId, { persist = true } = {}) {
    selectedThemeId = normalizeTheme(themeId);
    applyTheme(selectedThemeId);
    syncThemePicker(selectedThemeId);
    if (persist) persistTheme(selectedThemeId);
  }

  function restoreSelectedTheme() {
    applyTheme(selectedThemeId);
  }

  function themeColors(themeId) {
    const previous = root.dataset.theme;
    root.dataset.theme = normalizeTheme(themeId);
    const styles = window.getComputedStyle(root);
    const colors = [
      styles.getPropertyValue("--theme-background").trim(),
      styles.getPropertyValue("--theme-accent").trim(),
      styles.getPropertyValue("--theme-text").trim(),
      styles.getPropertyValue("--theme-ui").trim(),
    ];
    if (previous) root.dataset.theme = previous;
    else delete root.dataset.theme;
    return colors;
  }

  function buildThemeOptions(container) {
    const fragment = document.createDocumentFragment();
    const pinnedThemeIds = ["sdsu", "sdsu-dark"];
    const orderedThemeIds = [
      ...pinnedThemeIds,
      ...Object.keys(themeNames).filter((themeId) => !pinnedThemeIds.includes(themeId)),
    ];

    orderedThemeIds.forEach((themeId) => {
      const themeName = themeNames[themeId];
      const button = document.createElement("button");
      button.className = "theme-option";
      button.type = "button";
      button.setAttribute("role", "radio");
      button.setAttribute("aria-checked", "false");
      button.dataset.themeOption = themeId;

      const name = document.createElement("span");
      name.className = "theme-option-name";
      name.textContent = themeName;
      button.appendChild(name);

      const swatches = document.createElement("span");
      swatches.className = "theme-swatches";
      swatches.setAttribute("aria-hidden", "true");
      themeColors(themeId).forEach((color) => {
        const swatch = document.createElement("i");
        swatch.style.setProperty("--swatch", color);
        swatches.appendChild(swatch);
      });
      button.appendChild(swatches);
      fragment.appendChild(button);
    });
    container.replaceChildren(fragment);
  }

  function setupThemePicker() {
    const picker = document.querySelector("#theme-picker");
    const toggle = document.querySelector("#theme-picker-toggle");
    const panel = document.querySelector("#theme-picker-panel");
    const themeOptions = panel?.querySelector(".theme-options");
    if (!picker || !toggle || !panel || !themeOptions) return;

    selectedThemeId = savedTheme();
    buildThemeOptions(themeOptions);
    setTheme(selectedThemeId, { persist: false });

    const closePicker = ({ focusToggle = false } = {}) => {
      restoreSelectedTheme();
      panel.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
      if (focusToggle) toggle.focus();
    };

    const openPicker = () => {
      panel.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
    };

    toggle.addEventListener("click", () => {
      if (panel.hidden) openPicker();
      else closePicker();
    });

    panel.querySelectorAll("[data-theme-option]").forEach((button) => {
      button.addEventListener("pointerenter", () => applyTheme(button.dataset.themeOption));
      button.addEventListener("focus", () => applyTheme(button.dataset.themeOption));
      button.addEventListener("blur", restoreSelectedTheme);
      button.addEventListener("click", () => {
        setTheme(button.dataset.themeOption);
        closePicker({ focusToggle: true });
      });
    });

    themeOptions.addEventListener("pointerleave", restoreSelectedTheme);

    document.addEventListener("pointerdown", (event) => {
      if (panel.hidden || picker.contains(event.target)) return;
      closePicker();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || panel.hidden) return;
      closePicker({ focusToggle: true });
    });
  }

  setupThemePicker();
})();
