(() => {
  "use strict";

  const CONSENT_KEY = "classcatalog_analytics_consent_v1";
  const GRANTED = "granted";
  const DENIED = "denied";

  function analyticsId() {
    return (
      document
        .querySelector('meta[name="classcatalog-google-analytics-id"]')
        ?.getAttribute("content")
        ?.trim() || ""
    );
  }

  function savedConsent() {
    try {
      return localStorage.getItem(CONSENT_KEY) || "";
    } catch (_error) {
      return "";
    }
  }

  function saveConsent(value) {
    try {
      localStorage.setItem(CONSENT_KEY, value);
    } catch (_error) {}
  }

  function setBannerVisible(visible) {
    const banner = document.querySelector("#cookie-consent-banner");
    if (!banner) return;
    banner.hidden = !visible;
  }

  function analyticsScriptLoaded() {
    return Boolean(
      document.querySelector('script[data-classcatalog-analytics="true"]')
    );
  }

  function enableAnalytics() {
    const measurementId = analyticsId();
    if (!measurementId || analyticsScriptLoaded()) return;

    window[`ga-disable-${measurementId}`] = false;
    window.dataLayer = window.dataLayer || [];
    window.gtag =
      window.gtag ||
      function gtag() {
        window.dataLayer.push(arguments);
      };

    window.gtag("js", new Date());
    window.gtag("config", measurementId);

    const script = document.createElement("script");
    script.async = true;
    script.src =
      "https://www.googletagmanager.com/gtag/js?id=" +
      encodeURIComponent(measurementId);
    script.dataset.classcatalogAnalytics = "true";
    document.head.appendChild(script);
  }

  function expireAnalyticsCookies() {
    const names = document.cookie
      .split(";")
      .map((part) => part.split("=")[0].trim())
      .filter((name) => name.startsWith("_ga") || name === "_gid");

    for (const name of names) {
      let cookie =
        `${encodeURIComponent(name)}=; Path=/; Max-Age=0; SameSite=Lax`;
      if (window.location.protocol === "https:") cookie += "; Secure";
      document.cookie = cookie;
    }
  }

  function disableAnalytics() {
    const measurementId = analyticsId();

    if (measurementId) {
      window[`ga-disable-${measurementId}`] = true;
    }

    if (typeof window.gtag === "function") {
      window.gtag("consent", "update", {
        analytics_storage: "denied",
      });
    }

    expireAnalyticsCookies();
  }

  function acceptAnalytics() {
    saveConsent(GRANTED);
    setBannerVisible(false);
    enableAnalytics();
  }

  function rejectAnalytics() {
    saveConsent(DENIED);
    disableAnalytics();
    setBannerVisible(false);
  }

  function openSettings() {
    setBannerVisible(true);
    const banner = document.querySelector("#cookie-consent-banner");
    banner?.querySelector("button")?.focus();
  }

  function createBanner() {
    if (document.querySelector("#cookie-consent-banner")) return;

    const banner = document.createElement("section");
    banner.id = "cookie-consent-banner";
    banner.className = "cookie-consent-banner";
    banner.setAttribute("aria-label", "Cookie preferences");
    banner.hidden = true;
    banner.innerHTML = `
      <div class="cookie-consent-inner">
        <p class="cookie-consent-copy">
          ClassCatalog uses necessary browser storage for preferences.
          With your permission, we also use Google Analytics to understand site usage.
          <a href="/privacy">Privacy Policy</a>
        </p>
        <div class="cookie-consent-actions">
          <button id="cookie-reject-analytics" class="cookie-consent-button" type="button">
            Reject analytics
          </button>
          <button id="cookie-accept-analytics" class="cookie-consent-button" type="button">
            Accept analytics
          </button>
        </div>
      </div>
    `;

    document.body.appendChild(banner);

    banner
      .querySelector("#cookie-reject-analytics")
      ?.addEventListener("click", rejectAnalytics);
    banner
      .querySelector("#cookie-accept-analytics")
      ?.addEventListener("click", acceptAnalytics);
  }

  function createSettingsButton() {
    if (document.querySelector("#cookie-settings-button")) return;

    const footer = document.querySelector("footer .footer-inner");
    if (!footer) return;

    const button = document.createElement("button");
    button.id = "cookie-settings-button";
    button.className = "footer-cookie-settings";
    button.type = "button";
    button.textContent = "Cookie settings";
    button.addEventListener("click", openSettings);
    footer.appendChild(button);
  }

  function init() {
    createBanner();
    createSettingsButton();

    const consent = savedConsent();

    if (consent === GRANTED) {
      enableAnalytics();
      return;
    }

    if (consent === DENIED) {
      disableAnalytics();
      return;
    }

    setBannerVisible(true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
