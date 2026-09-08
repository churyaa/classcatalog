const state = {
  selectedTerm: "",
  options: null,
  controller: null,
  debounceTimer: null,
  page: 1,
  pageSize: 50,
  totalPages: 1,
  catalogStatus: null,
  profileController: null,
  profileDebounceTimer: null,
  completedCourseSuggestions: [],
  completedCourseSuggestionIndex: -1,
  adminSeatCourseSuggestions: [],
  adminSeatCourseSuggestionIndex: -1,
  adminProfessorSuggestions: [],
  adminProfessorSuggestionIndex: -1,
  adminHealth: null,
  adminEnabled: false,
  adminAuthenticated: false,
  seatPollTimer: null,
  seatPollSeconds: 30,
  seatStaleAfterSeconds: 1800,
  seatRefreshStatus: null,
  serviceMessages: {},
};

const themeCookieName = "classcatalog_theme";
const themeCookieMaxAgeSeconds = 60 * 60 * 24 * 365;
const legacyThemeStorageKey = "classcatalog_theme_v1";
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
  "rgb": "rgb",
  "rose-pine": "rose pine",
  "rose-pine-moon": "rose pine moon",
  "ryujinscales": "ryujinscales",
  "sewing-tin": "sewing tin",
  "shadow": "shadow",
  "solarized-dark": "solarized dark",
  "sonokai": "sonokai",
  "superuser": "superuser",
  "trance": "trance",
  "tron-orange": "tron orange",
  "voc": "voc",
  "watermelon": "watermelon",
  "wavez": "wavez",
  "8008": "8008",
  "9009": "9009",
  "alduin": "alduin",
  "neon-sunset": "Neon Sunset",
};

function savedTheme() {
  const cookieTheme = readCookie(themeCookieName);
  if (cookieTheme) return cookieTheme;

  // One-time migration for browsers that saved a theme before cookie persistence.
  try {
    const legacyTheme = window.localStorage.getItem(legacyThemeStorageKey) || "";
    if (legacyTheme) {
      persistTheme(legacyTheme);
      window.localStorage.removeItem(legacyThemeStorageKey);
      return legacyTheme;
    }
  } catch {
    // Ignore unavailable legacy storage and fall back to the default theme.
  }
  return "sdsu";
}

function persistTheme(themeId) {
  writeCookie(themeCookieName, normalizeTheme(themeId), themeCookieMaxAgeSeconds);
  try {
    window.localStorage.removeItem(legacyThemeStorageKey);
  } catch {
    // Legacy storage cleanup is optional.
  }
}

let selectedThemeId = "sdsu";

function normalizeTheme(themeId) {
  return Object.hasOwn(themeNames, themeId) ? themeId : "sdsu";
}

function applyTheme(themeId) {
  document.documentElement.dataset.theme = normalizeTheme(themeId);
  window.ClassCatalogThemeFavicon?.sync();
  if (window.ClassCatalogBrand) window.ClassCatalogBrand.syncThemeAssets();
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

function previewTheme(themeId) {
  applyTheme(themeId);
}

function restoreSelectedTheme() {
  applyTheme(selectedThemeId);
}

function setupThemePicker() {
  const picker = document.querySelector("#theme-picker");
  const toggle = document.querySelector("#theme-picker-toggle");
  const panel = document.querySelector("#theme-picker-panel");
  if (!picker || !toggle || !panel) return;

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

  setTheme(savedTheme(), { persist: false });

  toggle.addEventListener("click", () => {
    if (panel.hidden) openPicker();
    else closePicker();
  });

  const themeOptions = panel.querySelector(".theme-options");
  panel.querySelectorAll("[data-theme-option]").forEach((button) => {
    button.addEventListener("pointerenter", () => {
      previewTheme(button.dataset.themeOption);
    });

    button.addEventListener("focus", () => {
      previewTheme(button.dataset.themeOption);
    });

    button.addEventListener("blur", restoreSelectedTheme);

    button.addEventListener("click", () => {
      setTheme(button.dataset.themeOption);
      closePicker({ focusToggle: true });
    });
  });

  if (themeOptions) {
    themeOptions.addEventListener("pointerleave", restoreSelectedTheme);
  }

  document.addEventListener("pointerdown", (event) => {
    if (panel.hidden || picker.contains(event.target)) return;
    closePicker();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || panel.hidden) return;
    closePicker({ focusToggle: true });
  });
}

class ApiError extends Error {
  constructor(message, { status = 0, code = "request_failed", requestId = "" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

function fallbackApiMessage(url) {
  const path = String(url || "").toLowerCase();
  if (path.includes("/api/seats") || path.includes("/api/admin/seats")) {
    return "Seat data is temporarily unavailable.";
  }
  if (path.includes("ratings") || path.includes("professor")) {
    return "Professor ratings could not be loaded.";
  }
  if (path.includes("/api/catalog") || path.includes("/api/profile")) {
    return "Catalog information is temporarily unavailable.";
  }
  if (path.includes("/api/classes")) {
    return "Class results are temporarily unavailable. Please try again.";
  }
  if (path.includes("/api/options")) {
    return "Class filters are temporarily unavailable. Please try again.";
  }
  if (path.includes("/api/admin")) {
    return "Admin data is temporarily unavailable.";
  }
  return "Something went wrong. Please try again.";
}

function safeErrorMessage(error, fallbackMessage = "Something went wrong. Please try again.") {
  return error instanceof ApiError ? error.message : fallbackMessage;
}

async function fetchJson(url, options = {}, { fallbackMessage = "", allowDetail = false } = {}) {
  const cleanFallback = fallbackMessage || fallbackApiMessage(url);
  let response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    throw new ApiError(cleanFallback);
  }

  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    if (response.ok) {
      throw new ApiError(cleanFallback, { status: response.status });
    }
  }

  if (!response.ok) {
    const apiError = payload?.error || {};
    const detailedMessage = allowDetail && typeof payload?.detail === "string" ? payload.detail : "";
    throw new ApiError(apiError.message || detailedMessage || cleanFallback, {
      status: response.status,
      code: apiError.code,
      requestId: apiError.request_id,
    });
  }
  return payload;
}

function renderServiceMessages() {
  const container = document.querySelector("#service-alerts");
  if (!container) return;
  const messages = [...new Set(Object.values(state.serviceMessages).filter(Boolean))];
  container.hidden = messages.length === 0;
  container.replaceChildren(...messages.map((message) => {
    const item = document.createElement("div");
    item.className = "service-alert";
    item.textContent = message;
    return item;
  }));
}

function setServiceMessage(key, message = "") {
  if (message) state.serviceMessages[key] = message;
  else delete state.serviceMessages[key];
  renderServiceMessages();
}

const labels = {
  major_prep: "Major preparation",
  major_course: "Major course",
  elective: "Major elective",
  letter: "Letter grade",
  credit_no_credit: "Credit / No Credit",
  letter_or_credit_no_credit: "Letter or Cr/NC",
  other: "Other",
  in_person: "In person",
  hybrid: "Hybrid",
  online_asynchronous: "Online (Asynchronous)",
  online_synchronous: "Online (Synchronous)",
  online_with_in_person_exams: "Online with in-person exams",
  open: "Open",
  waitlist: "Waitlist",
  closed: "Closed",
  unknown: "Unknown",
};

const dayLabels = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };

const fallbackClassifications = ["major_prep", "major_course", "elective"];
const majorProgramCookieName = "classcatalog_major_program";
const majorProgramCookieMaxAgeSeconds = 60 * 60 * 24 * 365;
const completedCoursesCookieName = "classcatalog_completed_courses";
const completedCoursesCookieMaxAgeSeconds = 60 * 60 * 24 * 365;
const favoritesStorageKey = "classcatalog_favorites_v1";

function readFavorites() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(favoritesStorageKey) || "{}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function writeFavorites(favorites) {
  try {
    window.localStorage.setItem(favoritesStorageKey, JSON.stringify(favorites));
  } catch {
    // Favorites are a convenience feature; browsing should keep working if storage is unavailable.
  }
}

function sectionFavoriteKey(section) {
  const schedules = [...new Set(
    ((section.linked_components || []).length > 1
      ? section.linked_components.map((component) => component.schedule_number)
      : [section.schedule_number])
      .map((value) => String(value || "").trim())
      .filter(Boolean),
  )].sort();
  return [String(section.term || "").trim(), normalizeCourseCode(section.course_code), schedules.join("+")].join("::");
}

function favoriteSections() {
  return Object.values(readFavorites()).filter((section) => section && section.course_code && section.schedule_number);
}

function isFavorite(section) {
  return Boolean(readFavorites()[sectionFavoriteKey(section)]);
}

function updateFavoritesCount() {
  const count = favoriteSections().length;
  const badge = document.querySelector("#favorites-count");
  if (badge) badge.textContent = count.toLocaleString();
}

function setFavoriteButtonState(button, section) {
  if (!button) return;
  const active = isFavorite(section);
  button.classList.toggle("is-favorite", active);
  button.setAttribute("aria-pressed", String(active));
  button.setAttribute("aria-label", `${active ? "Remove" : "Add"} ${section.course_code} ${section.title} ${active ? "from" : "to"} favorites`);
}

function syncFavoriteButtons(section) {
  const key = sectionFavoriteKey(section);
  document.querySelectorAll(".course-favorite").forEach((button) => {
    if (button.dataset.favoriteKey === key) setFavoriteButtonState(button, section);
  });
}

function toggleFavorite(section) {
  const favorites = readFavorites();
  const key = sectionFavoriteKey(section);
  if (favorites[key]) delete favorites[key];
  else favorites[key] = section;
  writeFavorites(favorites);
  updateFavoritesCount();
  syncFavoriteButtons(section);
  if (window.location.hash === "#favorites") renderFavoritesPage();
}

function favoriteScheduleNumbers() {
  return [...new Set(favoriteSections().flatMap((section) => [
    section.schedule_number,
    ...(section.linked_components || []).map((component) => component.schedule_number),
  ]).map((value) => String(value || "").trim()).filter(Boolean))];
}

function seatRecordKey(section) {
  return `${String(section?.term || "").trim()}::${String(section?.schedule_number || "").trim()}`;
}

function applySeatRecord(target, record) {
  if (!target || !record) return target;
  return {
    ...target,
    seat_status: record.seat_status ?? target.seat_status,
    seats_available: record.seats_available ?? target.seats_available,
    seat_capacity: record.seat_capacity ?? target.seat_capacity,
    seats_enrolled: record.seats_enrolled ?? target.seats_enrolled,
    seat_updated_at: record.updated_at ?? target.seat_updated_at,
  };
}

function applySeatRecordsToFavorite(section, records) {
  const primary = applySeatRecord(section, records[seatRecordKey(section)]);
  const linked = (section.linked_components || []).map((component) =>
    applySeatRecord(component, records[seatRecordKey({ ...component, term: section.term })])
  );
  return { ...primary, linked_components: linked };
}

async function refreshFavoritesSeats() {
  const schedules = favoriteScheduleNumbers();
  if (!schedules.length) return;
  const params = new URLSearchParams();
  schedules.slice(0, 250).forEach((schedule) => params.append("schedule_number", schedule));
  try {
    const payload = await fetchJson(`/api/seats?${params.toString()}`, { cache: "no-store" });
    const records = payload.records || {};
    const favorites = readFavorites();
    Object.entries(favorites).forEach(([key, section]) => {
      favorites[key] = applySeatRecordsToFavorite(section, records);
    });
    writeFavorites(favorites);
    setServiceMessage("favorite-seats");
    if (window.location.hash === "#favorites") renderFavoritesPage();
  } catch (error) {
    // Keep the last known favorite seat counts when the lightweight poll fails.
    setServiceMessage("favorite-seats", safeErrorMessage(error, "Seat data is temporarily unavailable."));
  }
}

function renderSeatFreshness(status) {
  state.seatRefreshStatus = status || null;
  const element = document.querySelector("#seat-freshness");
  if (!element) return;
  element.classList.remove("is-fresh", "is-stale");
  if (!status?.enabled) {
    element.textContent = "Live seat refresh unavailable";
    return;
  }
  const age = Number(status.age_seconds);
  if (!Number.isFinite(age)) {
    element.textContent = "Live seat refresh starting…";
    return;
  }
  if (age < 90) {
    element.textContent = "Seats updated just now";
    element.classList.add("is-fresh");
    return;
  }
  const minutes = Math.max(1, Math.round(age / 60));
  element.textContent = `Seats updated ${minutes} min ago`;
  element.classList.add(age <= state.seatStaleAfterSeconds ? "is-fresh" : "is-stale");
}

async function loadSeatRefreshStatus() {
  try {
    const status = await fetchJson("/api/seats/status", { cache: "no-store" });
    const poll = Number(status.browser_poll_seconds);
    if (Number.isFinite(poll) && poll >= 15) state.seatPollSeconds = poll;
    const staleAfter = Number(status.stale_after_seconds);
    if (Number.isFinite(staleAfter) && staleAfter >= 60) state.seatStaleAfterSeconds = staleAfter;
    setServiceMessage("seat-status");
    renderSeatFreshness(status);
  } catch (error) {
    const message = safeErrorMessage(error, "Seat data is temporarily unavailable.");
    const element = document.querySelector("#seat-freshness");
    if (element) {
      element.classList.remove("is-fresh");
      element.classList.add("is-stale");
      element.textContent = message;
    }
    setServiceMessage("seat-status", message);
  }
}

async function pollSeatData() {
  await loadSeatRefreshStatus();
  if (window.location.hash === "#favorites") {
    await refreshFavoritesSeats();
    return;
  }
  if (!window.location.hash || window.location.hash === "#") {
    await loadCourses({ page: state.page, quiet: true });
  }
}

function startSeatPolling() {
  window.clearInterval(state.seatPollTimer);
  state.seatPollTimer = window.setInterval(() => { void pollSeatData(); }, state.seatPollSeconds * 1000);
}

function readCookie(name) {
  const encodedName = `${encodeURIComponent(name)}=`;
  const match = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(encodedName));
  if (!match) return "";
  try {
    return decodeURIComponent(match.slice(encodedName.length));
  } catch {
    return "";
  }
}

function writeCookie(name, value, maxAgeSeconds) {
  let cookie = `${encodeURIComponent(name)}=${encodeURIComponent(value)}; Path=/; Max-Age=${maxAgeSeconds}; SameSite=Lax`;
  if (window.location.protocol === "https:") cookie += "; Secure";
  document.cookie = cookie;
}

function savedMajorProgram() {
  return readCookie(majorProgramCookieName);
}

function persistMajorProgram(program) {
  const selectedProgram = String(program ?? "").trim();
  if (selectedProgram) {
    writeCookie(majorProgramCookieName, selectedProgram, majorProgramCookieMaxAgeSeconds);
    return;
  }
  writeCookie(majorProgramCookieName, "", 0);
}

function savedCompletedCourses() {
  return readCookie(completedCoursesCookieName);
}

function persistCompletedCourses(value) {
  const completedCourses = String(value ?? "").trim();
  if (completedCourses) {
    writeCookie(completedCoursesCookieName, completedCourses, completedCoursesCookieMaxAgeSeconds);
    return;
  }
  writeCookie(completedCoursesCookieName, "", 0);
}

function normalizeCourseCode(value) {
  return String(value ?? "").trim().replace(/\s+/g, " ").toUpperCase();
}

function completedCourseValues() {
  return document.querySelector("#completed-courses").value
    .split(",")
    .map((item) => normalizeCourseCode(item))
    .filter(Boolean);
}

function courseLookupOptions() {
  return Array.isArray(state.options?.courses) ? state.options.courses : [];
}

function courseLookupByCode(code) {
  const normalized = normalizeCourseCode(code);
  return courseLookupOptions().find((course) => normalizeCourseCode(course.course_code) === normalized) || null;
}

function closeCompletedCourseSuggestions() {
  const box = document.querySelector("#completed-course-suggestions");
  const input = document.querySelector("#completed-course-input");
  if (!box || !input) return;
  box.hidden = true;
  box.replaceChildren();
  input.setAttribute("aria-expanded", "false");
  input.removeAttribute("aria-activedescendant");
  state.completedCourseSuggestions = [];
  state.completedCourseSuggestionIndex = -1;
}

function renderCompletedCourseChips() {
  const container = document.querySelector("#completed-course-chips");
  if (!container) return;
  container.replaceChildren();
  completedCourseValues().forEach((courseCode) => {
    const chip = document.createElement("span");
    chip.className = "completed-course-chip";
    const text = document.createElement("span");
    text.textContent = courseCode;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "completed-course-remove";
    remove.setAttribute("aria-label", `Remove ${courseCode} from completed courses`);
    remove.textContent = "×";
    remove.addEventListener("click", () => removeCompletedCourse(courseCode));
    chip.append(text, remove);
    container.appendChild(chip);
  });
}

function setCompletedCourseValues(values, { refresh = true } = {}) {
  const unique = [];
  const seen = new Set();
  values.forEach((value) => {
    const normalized = normalizeCourseCode(value);
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    unique.push(normalized);
  });
  const hidden = document.querySelector("#completed-courses");
  hidden.value = unique.join(", ");
  persistCompletedCourses(hidden.value);
  renderCompletedCourseChips();
  if (refresh) {
    scheduleProgramSummary();
    scheduleLoad();
  }
}

function addCompletedCourse(courseCode) {
  const normalized = normalizeCourseCode(courseCode);
  if (!normalized) return;
  setCompletedCourseValues([...completedCourseValues(), normalized]);
  const input = document.querySelector("#completed-course-input");
  if (input) input.value = "";
  closeCompletedCourseSuggestions();
}

function removeCompletedCourse(courseCode) {
  const normalized = normalizeCourseCode(courseCode);
  setCompletedCourseValues(completedCourseValues().filter((value) => value !== normalized));
}

function matchingCourseOptions(query, excludedCodes = new Set(), allowedCodes = null) {
  const needle = String(query ?? "").trim().toLowerCase();
  if (!needle) return [];
  return courseLookupOptions()
    .filter((course) => !excludedCodes.has(normalizeCourseCode(course.course_code)))
    .filter((course) => allowedCodes === null || allowedCodes.has(normalizeCourseCode(course.course_code)))
    .map((course) => {
      const code = String(course.course_code ?? "");
      const title = String(course.title ?? "");
      const codeLower = code.toLowerCase();
      const titleLower = title.toLowerCase();
      let score = 99;
      if (codeLower.startsWith(needle)) score = 0;
      else if (titleLower.startsWith(needle)) score = 1;
      else if (codeLower.includes(needle)) score = 2;
      else if (titleLower.includes(needle)) score = 3;
      return { course, score };
    })
    .filter((item) => item.score < 99)
    .sort((left, right) => left.score - right.score || left.course.course_code.localeCompare(right.course.course_code))
    .slice(0, 8)
    .map((item) => item.course);
}

function matchingCompletedCourseOptions(query) {
  return matchingCourseOptions(query, new Set(completedCourseValues()));
}

function matchingAdminSeatCourseOptions(query) {
  const refreshableCodes = new Set(
    (state.adminHealth?.seat_refresh?.refreshable_course_codes || []).map(normalizeCourseCode),
  );
  return matchingCourseOptions(query, new Set(), refreshableCodes);
}

function normalizeProfessorName(value) {
  return String(value ?? "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

function adminProfessorEditableOptions() {
  const professors = state.adminHealth?.professors || {};
  const unmatched = Array.isArray(professors.unmatched_instructors)
    ? professors.unmatched_instructors
    : [];
  const overrides = Array.isArray(professors.manual_overrides)
    ? professors.manual_overrides.map((record) => ({
        name: record.name,
        normalized_name: record.normalized_name,
        course_codes: record.course_codes || [],
        manual_override: true,
      }))
    : [];
  const unique = new Map();
  [...unmatched, ...overrides].forEach((item) => {
    const key = normalizeProfessorName(item.normalized_name || item.name);
    if (key) unique.set(key, item);
  });
  return [...unique.values()];
}

function matchingAdminProfessorOptions(query) {
  const needle = normalizeProfessorName(query);
  if (!needle) return [];
  return adminProfessorEditableOptions()
    .map((item) => {
      const name = normalizeProfessorName(item.name);
      const courses = (item.course_codes || []).join(" ").toLowerCase();
      let score = 99;
      if (name.startsWith(needle)) score = 0;
      else if (name.includes(needle)) score = 1;
      else if (courses.includes(needle)) score = 2;
      return { item, score };
    })
    .filter(({ score }) => score < 99)
    .sort((left, right) => left.score - right.score || String(left.item.name).localeCompare(String(right.item.name)))
    .slice(0, 8)
    .map(({ item }) => item);
}

function updateCompletedCourseSuggestionHighlight() {
  const box = document.querySelector("#completed-course-suggestions");
  const input = document.querySelector("#completed-course-input");
  if (!box || !input) return;
  [...box.querySelectorAll(".completed-course-suggestion")].forEach((button, index) => {
    const active = index === state.completedCourseSuggestionIndex;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
    if (active) input.setAttribute("aria-activedescendant", button.id);
  });
  if (state.completedCourseSuggestionIndex < 0) input.removeAttribute("aria-activedescendant");
}

function renderCompletedCourseSuggestions() {
  const input = document.querySelector("#completed-course-input");
  const box = document.querySelector("#completed-course-suggestions");
  if (!input || !box) return;
  const matches = matchingCompletedCourseOptions(input.value);
  state.completedCourseSuggestions = matches;
  state.completedCourseSuggestionIndex = matches.length ? 0 : -1;
  box.replaceChildren();
  if (!matches.length) {
    closeCompletedCourseSuggestions();
    return;
  }
  matches.forEach((course, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "completed-course-suggestion";
    button.id = `completed-course-option-${index}`;
    button.setAttribute("role", "option");
    const code = document.createElement("strong");
    code.textContent = course.course_code;
    const title = document.createElement("span");
    title.textContent = course.title || "Course title unavailable";
    button.append(code, title);
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => {
      addCompletedCourse(course.course_code);
      input.focus();
    });
    box.appendChild(button);
  });
  box.hidden = false;
  input.setAttribute("aria-expanded", "true");
  updateCompletedCourseSuggestionHighlight();
}

function handleCompletedCourseKeydown(event) {
  const input = event.currentTarget;
  const suggestions = state.completedCourseSuggestions;
  if (event.key === "ArrowDown" && suggestions.length) {
    event.preventDefault();
    state.completedCourseSuggestionIndex = (state.completedCourseSuggestionIndex + 1) % suggestions.length;
    updateCompletedCourseSuggestionHighlight();
    return;
  }
  if (event.key === "ArrowUp" && suggestions.length) {
    event.preventDefault();
    state.completedCourseSuggestionIndex = (state.completedCourseSuggestionIndex - 1 + suggestions.length) % suggestions.length;
    updateCompletedCourseSuggestionHighlight();
    return;
  }
  if (event.key === "Escape") {
    closeCompletedCourseSuggestions();
    return;
  }
  if (event.key === "Backspace" && !input.value && completedCourseValues().length) {
    const values = completedCourseValues();
    removeCompletedCourse(values[values.length - 1]);
    return;
  }
  if (event.key !== "Enter" && event.key !== ",") return;
  event.preventDefault();
  const selected = suggestions[state.completedCourseSuggestionIndex] || suggestions[0] || null;
  if (selected) {
    addCompletedCourse(selected.course_code);
    return;
  }
  const exact = courseLookupByCode(input.value);
  const fallbackCode = normalizeCourseCode(input.value);
  if (exact) addCompletedCourse(exact.course_code);
  else if (/^[A-Z][A-Z &-]*\s+\d+[A-Z]?$/.test(fallbackCode)) addCompletedCourse(fallbackCode);
}

function setupCompletedCoursePicker() {
  const input = document.querySelector("#completed-course-input");
  if (!input) return;
  input.addEventListener("input", renderCompletedCourseSuggestions);
  input.addEventListener("focus", renderCompletedCourseSuggestions);
  input.addEventListener("keydown", handleCompletedCourseKeydown);
  input.addEventListener("blur", () => window.setTimeout(closeCompletedCourseSuggestions, 120));
  renderCompletedCourseChips();
}

function restoreCompletedCourses() {
  const input = document.querySelector("#completed-courses");
  if (!input || input.value) return;
  input.value = savedCompletedCourses();
  renderCompletedCourseChips();
}

const fallbackRequirements = [
  "GWAR",
  "American Institutions",
  "Ethnic Studies",
  "Cultural Diversity",
  "1A English Composition",
  "1B Critical Thinking",
  "1C Oral Communication",
  "2 Mathematical/Quantitative Reasoning",
  "3A Arts",
  "3B Humanities",
  "4 Social and Behavioral Sciences",
  "5A Physical Science",
  "5B Biological Science",
  "5C Laboratory",
  "EXPLORATIONS - PHYS, BIO, MATH/QUANT",
  "EXPLORATIONS - ARTS & HUMANITIES",
  "EXPLORATIONS - SOCIAL & BEHAVIORAL SCIENCES",
];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  })[character]);
}

function checkedValues(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map((input) => input.value);
}

function dayValues(stateName) {
  return [...document.querySelectorAll(`#days .day-toggle[data-state="${stateName}"]`)]
    .map((button) => button.dataset.day)
    .filter(Boolean);
}

function addRepeated(params, key, values) {
  values.forEach((value) => params.append(key, value));
}

function addIfPresent(params, key, value) {
  if (value !== "" && value !== null && value !== undefined) params.set(key, value);
}

function buildParams(page = state.page) {
  const params = new URLSearchParams();
  if (state.selectedTerm) params.append("term", state.selectedTerm);
  addRepeated(params, "campus", checkedValues("campus"));
  addIfPresent(params, "q", document.querySelector("#query").value.trim());
  addRepeated(params, "requirement", checkedValues("requirement"));
  addIfPresent(params, "units_min", document.querySelector("#units-min").value);
  addIfPresent(params, "units_max", document.querySelector("#units-max").value);
  addRepeated(params, "grading", checkedValues("grading"));
  addIfPresent(params, "program", document.querySelector("#program").value);
  addIfPresent(params, "catalog_year", document.querySelector("#catalog-year").value);
  addRepeated(params, "classification", checkedValues("classification"));
  params.set("major_only", String(document.querySelector("#major-only").checked));

  const completed = document.querySelector("#completed-courses").value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  addRepeated(params, "completed_course", completed);

  addRepeated(params, "day", dayValues("include"));
  addRepeated(params, "exclude_day", dayValues("exclude"));
  addIfPresent(params, "time_from", document.querySelector("#time-from").value);
  addIfPresent(params, "time_to", document.querySelector("#time-to").value);
  addRepeated(params, "instruction_mode", checkedValues("instruction_mode"));
  addRepeated(params, "seat_status", checkedValues("seat_status"));
  addIfPresent(params, "rating_min", document.querySelector("#rating-min").value);
  addIfPresent(params, "difficulty_max", document.querySelector("#difficulty-max").value);
  addIfPresent(params, "would_take_again_min", document.querySelector("#would-take-again-min").value);
  addIfPresent(params, "reviews_min", document.querySelector("#reviews-min").value);
  params.set("sort_by", document.querySelector("#sort-by").value);
  params.set("page", String(page));
  params.set("page_size", String(state.pageSize));
  return params;
}

function checkbox(container, name, value, text, { disabled = false, checked = false } = {}) {
  const label = document.createElement("label");
  label.className = disabled ? "check-row unavailable-option" : "check-row";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.name = name;
  input.value = value;
  input.disabled = disabled;
  input.checked = checked;
  const span = document.createElement("span");
  span.textContent = text;
  label.append(input, span);
  container.appendChild(label);
}

function renderUnavailableOptions(container, name, values, textForValue, message) {
  values.forEach((value) => checkbox(container, name, value, textForValue(value), { disabled: true }));
  const note = document.createElement("p");
  note.className = "microcopy filter-unavailable-note";
  note.textContent = message;
  container.appendChild(note);
}

function populateSelect(select, values, emptyLabel) {
  select.replaceChildren();
  const emptyOption = document.createElement("option");
  emptyOption.value = "";
  emptyOption.textContent = emptyLabel;
  select.appendChild(emptyOption);
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  });
}

function populateOptions(options) {
  state.options = options;
  const termContainer = document.querySelector("#term-buttons");
  termContainer.replaceChildren();
  const allTerms = ["", ...options.terms];
  allTerms.forEach((term) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = term || "All terms";
    button.dataset.term = term;
    button.setAttribute("aria-pressed", String(term === state.selectedTerm));
    button.addEventListener("click", () => {
      state.selectedTerm = term;
      [...termContainer.children].forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.term === term)));
      scheduleLoad();
    });
    termContainer.appendChild(button);
  });

  const program = document.querySelector("#program");
  const priorProgram = program.value;
  const cookieProgram = savedMajorProgram();
  const preferredProgram = priorProgram || cookieProgram;
  populateSelect(program, options.programs, "Any program");
  if (options.programs.includes(preferredProgram)) {
    program.value = preferredProgram;
  } else if (cookieProgram && options.programs.length) {
    persistMajorProgram("");
  }
  program.disabled = options.programs.length === 0;

  const catalogYear = document.querySelector("#catalog-year");
  const priorCatalogYear = catalogYear.value;
  populateSelect(catalogYear, options.catalog_years, "Any year");
  if (options.catalog_years.includes(priorCatalogYear)) catalogYear.value = priorCatalogYear;
  if (program.value && !catalogYear.value && options.catalog_years.length === 1) {
    catalogYear.value = options.catalog_years[0];
  }
  catalogYear.disabled = options.catalog_years.length === 0;

  const campuses = document.querySelector("#campuses");
  const requirements = document.querySelector("#requirements");
  const classifications = document.querySelector("#classifications");
  const gradings = document.querySelector("#gradings");
  const formats = document.querySelector("#formats");
  const seatStatuses = document.querySelector("#seat-statuses");
  campuses.replaceChildren();
  requirements.replaceChildren();
  classifications.replaceChildren();
  gradings.replaceChildren();
  formats.replaceChildren();
  seatStatuses.replaceChildren();

  const campusOptions = options.campuses?.length
    ? options.campuses
    : ["San Diego Campus", "Imperial Valley Campus"];
  campusOptions.forEach((value) => checkbox(
    campuses,
    "campus",
    value,
    value,
    { checked: value === "San Diego Campus" },
  ));

  if (options.requirements.length) {
    options.requirements.forEach((value) => checkbox(requirements, "requirement", value, value));
  } else {
    renderUnavailableOptions(
      requirements,
      "requirement",
      fallbackRequirements,
      (value) => value,
      "Requirement mappings are not loaded for this schedule yet. These options will activate when catalog requirement data is added.",
    );
  }

  if (options.classifications.length) {
    options.classifications.forEach((value) => checkbox(classifications, "classification", value, labels[value] || value));
    const note = document.createElement("p");
    note.id = "classification-context-note";
    note.className = "microcopy filter-unavailable-note";
    note.textContent = "Choose a major and catalog year to scope classifications to one SDSU degree program.";
    classifications.appendChild(note);
  } else {
    renderUnavailableOptions(
      classifications,
      "classification",
      fallbackClassifications,
      (value) => labels[value] || value,
      "Program classification mappings are not loaded yet. These options will activate when degree-program data is added.",
    );
  }
  options.gradings.forEach((value) => checkbox(gradings, "grading", value, labels[value] || value));
const instructionModeOptions = [
  "in_person",
  "hybrid",
  "online_asynchronous",
  "online_synchronous",
  "online_with_in_person_exams",
];

instructionModeOptions.forEach((value) =>
  checkbox(
    formats,
    "instruction_mode",
    value,
    labels[value] || value
  )
);  options.seat_statuses.filter((value) => value !== "unknown").forEach((value) => checkbox(seatStatuses, "seat_status", value, labels[value] || value));

  const days = document.querySelector("#days");
  days.replaceChildren();
  Object.entries(dayLabels).forEach(([value, text]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "day-toggle";
    button.dataset.day = value;
    button.dataset.state = "off";
    button.textContent = text;
    button.setAttribute("aria-label", `${text}: not filtered. Click to include.`);
    button.addEventListener("click", () => {
      const nextState = button.dataset.state === "off"
        ? "include"
        : button.dataset.state === "include"
          ? "exclude"
          : "off";
      button.dataset.state = nextState;
      button.setAttribute(
        "aria-label",
        nextState === "include"
          ? `${text}: included. Click to exclude.`
          : nextState === "exclude"
            ? `${text}: excluded. Click to clear.`
            : `${text}: not filtered. Click to include.`,
      );
      scheduleLoad();
    });
    days.appendChild(button);
  });
  const dayHelp = document.createElement("p");
  dayHelp.className = "microcopy day-filter-help";
  dayHelp.textContent = "Click once to include a day, twice to exclude it, and a third time to clear it.";
  days.insertAdjacentElement("afterend", dayHelp);
  syncClassificationAvailability();
}

function syncClassificationAvailability() {
  const hasCatalogMappings = Boolean(state.options?.classifications?.length);
  const program = document.querySelector("#program").value;
  const catalogYear = document.querySelector("#catalog-year").value;
  const enabled = hasCatalogMappings && Boolean(program && catalogYear);
  document.querySelectorAll('input[name="classification"]').forEach((input) => {
    if (!enabled) input.checked = false;
    input.disabled = !enabled;
    input.closest("label")?.classList.toggle("unavailable-option", !enabled);
  });
  const note = document.querySelector("#classification-context-note");
  if (note) {
    note.textContent = enabled
      ? `Classifications are scoped to ${program} (${catalogYear}).`
      : "Choose a major and catalog year to scope classifications to one SDSU degree program.";
  }
}

function renderCatalogStatus(status) {
  state.catalogStatus = status;
  const container = document.querySelector("#catalog-status");
  if (!container) return;
  if (!status.loaded) {
    container.className = "catalog-status";
    container.innerHTML = "<strong>Degree mappings not loaded</strong><span>Install the public SDSU catalog overlay to activate planning filters.</span>";
    return;
  }
  container.className = "catalog-status catalog-status-loaded";
  const yearText = status.catalog_years.length ? status.catalog_years.join(", ") : "catalog year unavailable";
  container.innerHTML = `<strong>SDSU catalog mappings loaded</strong><span>${status.programs.toLocaleString()} programs · ${status.requirements.toLocaleString()} requirements · ${escapeHtml(yearText)}</span>`;
}

function hideProgramSummary() {
  const summary = document.querySelector("#program-summary");
  if (summary) summary.hidden = true;
}

function renderProgramSummary(summary) {
  const container = document.querySelector("#program-summary");
  document.querySelector("#program-summary-title").textContent = `${summary.program} · ${summary.catalog_year}`;
  const source = document.querySelector("#program-summary-source");
  source.href = summary.source_url;
  document.querySelector("#program-summary-counts").textContent =
    `${summary.completed_required_course_count.toLocaleString()}/${summary.required_course_count.toLocaleString()} required courses completed:`;
  const preview = summary.remaining_scheduled_courses.slice(0, 8);
  document.querySelector("#program-summary-remaining").textContent = preview.length
    ? `Next mapped schedule options: ${preview.join(", ")}${summary.remaining_scheduled_courses.length > preview.length ? "…" : ""}`
    : "No remaining mapped courses are present in the loaded schedule.";
  container.hidden = false;
}

async function updateProgramSummary() {
  const program = document.querySelector("#program").value;
  const catalogYear = document.querySelector("#catalog-year").value;
  if (!program || !catalogYear || !state.catalogStatus?.loaded) {
    setServiceMessage("profile-summary");
    hideProgramSummary();
    return;
  }
  if (state.profileController) state.profileController.abort();
  const controller = new AbortController();
  state.profileController = controller;
  const params = new URLSearchParams({ program, catalog_year: catalogYear });
  completedCourseValues().forEach((course) => params.append("completed_course", course));
  try {
    const summary = await fetchJson(`/api/profile/summary?${params.toString()}`, { signal: controller.signal });
    setServiceMessage("profile-summary");
    renderProgramSummary(summary);
  } catch (error) {
    if (error?.name !== "AbortError") {
      hideProgramSummary();
      setServiceMessage("profile-summary", safeErrorMessage(error, "Catalog information is temporarily unavailable."));
    }
  } finally {
    if (state.profileController === controller) state.profileController = null;
  }
}

function scheduleProgramSummary() {
  window.clearTimeout(state.profileDebounceTimer);
  state.profileDebounceTimer = window.setTimeout(updateProgramSummary, 180);
}

function formatTime(value) {
  if (!value) return "TBA";
  const [hourText, minute] = value.split(":");
  const hour = Number(hourText);
  const suffix = hour >= 12 ? "PM" : "AM";
  const displayHour = hour % 12 || 12;
  return `${displayHour}:${minute} ${suffix}`;
}

function meetingText(meetings) {
  if (!meetings.length) return "Asynchronous / TBA";
  return meetings.map((meeting) => {
    const days = meeting.days.length ? meeting.days.map((day) => dayLabels[day]).join("/") : "TBA";
    if (!meeting.start_time || !meeting.end_time) return days;
    return `${days} · ${formatTime(meeting.start_time)}–${formatTime(meeting.end_time)}`;
  }).join("; ");
}

function meetingLocationText(meetings, fallbackLocation = null) {
  const rows = meetings
    .filter((meeting) => meeting.location)
    .map((meeting) => {
      const days = meeting.days.length ? meeting.days.map((day) => dayLabels[day]).join("/") : "Meeting";
      return `${days}: ${meeting.location}`;
    });
  if (rows.length) return [...new Set(rows)].join("; ");
  return fallbackLocation || "Online / TBA";
}

function professorProfileLink(instructor, metrics, className = "professor-name") {
  const label = escapeHtml(instructor || "Instructor TBA");
  const url = metrics?.profile_url;
  if (!url) return `<span class="${className}">${label}</span>`;
  return `<a class="${className} professor-profile-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" title="Open RateMyProfessors profile">${label}<span class="external-link-mark" aria-hidden="true">↗</span></a>`;
}

function rmpMetricTone(value, metric) {
  if (value == null || !Number.isFinite(Number(value))) return "neutral";
  const numeric = Number(value);

  if (metric === "quality") {
    if (numeric >= 4) return "good";
    if (numeric >= 2.5) return "mixed";
    return "poor";
  }

  if (metric === "difficulty") {
    if (numeric <= 2) return "good";
    if (numeric <= 3.5) return "mixed";
    return "poor";
  }

  if (metric === "take-again") {
    if (numeric >= 80) return "good";
    if (numeric >= 50) return "mixed";
    return "poor";
  }

  return "neutral";
}

function professorPanel(section) {
  if (!section.instructor) return `<div class="professor-panel"><p class="professor-name">Instructor TBA</p><p class="microcopy">No RateMyProfessors match available.</p></div>`;
  const metrics = section.professor;
  const professorName = professorProfileLink(section.instructor, metrics);
  if (!metrics) return `<div class="professor-panel"><p>${professorName}</p><p class="microcopy">No RateMyProfessors match available.</p></div>`;
  const quality = metrics.rating == null ? "—" : `${metrics.rating.toFixed(1)} / 5`;
  const difficulty = metrics.difficulty == null ? "—" : `${metrics.difficulty.toFixed(1)} / 5`;
  const takeAgain = metrics.would_take_again_percent == null ? "—" : `${Math.round(metrics.would_take_again_percent)}%`;
  return `
    <div class="professor-panel">
      <p>${professorName}</p>
      <p class="rmp-source-label">RateMyProfessors</p>
      <div class="professor-metrics rmp-stat-list">
        <div class="rmp-stat-row"><span>Reviews</span><strong class="rmp-score neutral">${metrics.num_reviews}</strong></div>
        <div class="rmp-stat-row"><span>Quality</span><strong class="rmp-score ${rmpMetricTone(metrics.rating, "quality")}">${quality}</strong></div>
        <div class="rmp-stat-row"><span>Professor Difficulty</span><strong class="rmp-score ${rmpMetricTone(metrics.difficulty, "difficulty")}">${difficulty}</strong></div>
        <div class="rmp-stat-row"><span>Take Again %</span><strong class="rmp-score ${rmpMetricTone(metrics.would_take_again_percent, "take-again")}">${takeAgain}</strong></div>
      </div>
    </div>`;
}

function seatSummary(section) {
  const capacity = section.seat_capacity;
  let enrolled = section.seats_enrolled;
  let available = section.seats_available;

  if (enrolled == null && capacity != null && available != null) {
    enrolled = Math.max(capacity - available, 0);
  }
  if (available == null && capacity != null && enrolled != null) {
    available = Math.max(capacity - enrolled, 0);
  }

  let status = section.seat_status;
  // Seat availability is more useful than a lagging PeopleSoft status label.
  // This makes Waitlist -> Open visible immediately when SDSU reports positive
  // available seats, even if the text status takes another refresh to catch up.
  if (available != null && available > 0) status = "open";
  if (status === "unknown") {
    if (available != null && available > 0) status = "open";
    else if (capacity != null && enrolled != null && enrolled < capacity) status = "open";
    else if (section.waitlist_available != null && section.waitlist_available > 0) status = "waitlist";
    else if (available === 0 || (capacity != null && enrolled != null && enrolled >= capacity)) status = "closed";
  }

  const statusLabel = status === "unknown"
    ? "Not posted"
    : labels[status] || status || "Not posted";
  const primary = capacity != null && enrolled != null
    ? `${statusLabel} ${enrolled}/${capacity}`
    : statusLabel;

  let secondary = "";
  if (status === "open" && available != null) {
    secondary = `${available} seat${available === 1 ? "" : "s"} available`;
  } else if (
    status === "waitlist"
    && section.waitlist_available != null
    && section.waitlist_available > 0
  ) {
    secondary = `${section.waitlist_available} waitlist spot${section.waitlist_available === 1 ? "" : "s"} available`;
  }
  return { primary, secondary, status };
}

function seatFreshnessDetails(updatedAt) {
  if (!updatedAt) {
    return {
      text: state.seatRefreshStatus?.enabled ? "Live update pending" : "Seat update time unavailable",
      tone: "pending",
      title: "Waiting for the live SDSU seat refresher to update this class.",
    };
  }
  const timestamp = Date.parse(updatedAt);
  if (!Number.isFinite(timestamp)) {
    return { text: "Seat update time unavailable", tone: "pending", title: "" };
  }
  const ageSeconds = Math.max(0, (Date.now() - timestamp) / 1000);
  const stale = ageSeconds > state.seatStaleAfterSeconds;
  let ageText;
  if (ageSeconds < 90) {
    ageText = "just now";
  } else if (ageSeconds < 3600) {
    ageText = `${Math.max(1, Math.round(ageSeconds / 60))} min ago`;
  } else {
    const hours = ageSeconds / 3600;
    ageText = `${hours < 10 ? hours.toFixed(1) : Math.round(hours)} hr ago`;
  }
  return {
    text: stale ? `Stale · updated ${ageText}` : `Updated ${ageText}`,
    tone: stale ? "stale" : "fresh",
    title: `Last live SDSU seat refresh: ${new Date(timestamp).toLocaleString()}`,
  };
}

function seatFreshnessMarkup(section, className = "seat-card-freshness") {
  const freshness = seatFreshnessDetails(section?.seat_updated_at);
  const warning = freshness.tone === "stale" ? `<span aria-hidden="true">⚠</span> ` : "";
  return `<span class="${className} ${freshness.tone}"${freshness.title ? ` title="${escapeHtml(freshness.title)}"` : ""}>${warning}${escapeHtml(freshness.text)}</span>`;
}

function componentSeatSummary(component) {
  return seatSummary(component);
}

function componentProfessorSummary(component) {
  const instructor = component.instructor || "TBA";
  const metrics = component.professor;
  const professorName = professorProfileLink(instructor, metrics, "component-professor-name");
  if (!metrics) {
    return `${professorName}<span class="component-professor-meta">No RateMyProfessors match</span>`;
  }
  const quality = metrics.rating == null ? "—" : `${metrics.rating.toFixed(1)} / 5`;
  const difficulty = metrics.difficulty == null ? "—" : `${metrics.difficulty.toFixed(1)} / 5`;
  const takeAgain = metrics.would_take_again_percent == null ? "—" : `${Math.round(metrics.would_take_again_percent)}%`;
  return `${professorName}<span class="component-professor-meta rmp-component-metrics"><span class="rmp-component-stat"><span>Reviews</span><strong class="rmp-score neutral">${metrics.num_reviews}</strong></span><span class="rmp-component-stat"><span>Quality</span><strong class="rmp-score ${rmpMetricTone(metrics.rating, "quality")}">${quality}</strong></span><span class="rmp-component-stat"><span>Professor Difficulty</span><strong class="rmp-score ${rmpMetricTone(metrics.difficulty, "difficulty")}">${difficulty}</strong></span><span class="rmp-component-stat"><span>Take Again %</span><strong class="rmp-score ${rmpMetricTone(metrics.would_take_again_percent, "take-again")}">${takeAgain}</strong></span></span>`;
}

function componentRows(section) {
  const components = section.linked_components || [];
  if (components.length < 2) return "";
  const rows = components.map((component) => {
    const seats = componentSeatSummary(component);
    const sectionText = component.section_number ? `Section ${component.section_number}` : "";
    return `
      <div class="component-row" role="row">
        <div class="component-class" role="cell">
          <strong class="component-name">${escapeHtml(component.component || "Class")}</strong>
          <span class="component-class-number">Class #${escapeHtml(component.schedule_number)}</span>
          ${sectionText ? `<span class="component-subvalue">${escapeHtml(sectionText)}</span>` : ""}
        </div>
        <div class="component-cell" role="cell">${escapeHtml(meetingText(component.meetings || []))}</div>
        <div class="component-cell" role="cell">${escapeHtml(meetingLocationText(component.meetings || [], component.location))}</div>
        <div class="component-cell component-professor" role="cell">${componentProfessorSummary(component)}</div>
        <div class="component-cell component-seats" role="cell">
          <span class="component-seat ${escapeHtml(seats.status)}">${escapeHtml(seats.primary)}</span>
          ${seats.secondary ? `<span class="component-subvalue">${escapeHtml(seats.secondary)}</span>` : ""}
          ${seatFreshnessMarkup(component, "component-seat-freshness")}
        </div>
      </div>`;
  }).join("");
  return `
    <div class="component-group" aria-label="Class option components">
      <div class="component-group-title">Class components</div>
      <div class="component-table" role="table" aria-label="Classes included in this option">
        <div class="component-header" role="row">
          <div role="columnheader">Class</div>
          <div role="columnheader">Meetings</div>
          <div role="columnheader">Location</div>
          <div role="columnheader">Professor</div>
          <div role="columnheader">Seats</div>
        </div>
        ${rows}
      </div>
    </div>`;
}

function courseCard(section) {
  const card = document.createElement("article");
  const grouped = (section.linked_components || []).length > 1;
  card.className = grouped ? "course-card course-card-grouped" : "course-card";
  const seats = seatSummary(section);
  const selectedProgram = document.querySelector("#program").value;
  const selectedCatalogYear = document.querySelector("#catalog-year").value;
  const programTags = selectedProgram
    ? [...new Set(
      section.program_tags
        .filter((tag) => (
          tag.program === selectedProgram
          && (!selectedCatalogYear || tag.catalog_year === selectedCatalogYear)
        ))
        .map((tag) => labels[tag.classification] || tag.classification),
    )]
    : [];

  const singleTags = [
    `<span class="badge ${seats.status}">${escapeHtml(labels[seats.status] || seats.status)}</span>`,
    `<span class="badge">${escapeHtml(labels[section.instruction_mode] || section.instruction_mode)}</span>`,
    ...section.requirement_tags.map((tag) => `<span class="badge">${escapeHtml(tag)}</span>`),
  ];
  programTags.forEach((tag) => singleTags.push(`<span class="badge">${escapeHtml(tag)}</span>`));

  const location = meetingLocationText(section.meetings || [], section.location);
  const description = section.description?.trim() || "Course description is not available yet.";
  const tooltipId = `course-description-${String(section.id).replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const seatSecondary = seats.secondary
    ? `<span class="data-subvalue">${escapeHtml(seats.secondary)}</span>`
    : "";
  const seatFreshness = seatFreshnessMarkup(section);
  const componentLabel = section.component ? `${section.component} · ` : "";
  const groupedMeta = grouped
    ? `${section.option_number ? `Option ${section.option_number} · ` : ""}${section.linked_components.length} class components · ${escapeHtml(section.units_text || String(section.units))} units · ${escapeHtml(labels[section.grading] || section.grading)}`
    : `${escapeHtml(componentLabel)}Section ${escapeHtml(section.section_number || "—")} · Schedule #${escapeHtml(section.schedule_number)} · ${escapeHtml(section.units_text || String(section.units))} units · ${escapeHtml(labels[section.grading] || section.grading)}`;

  const groupedTags = [
    `<span class="badge ${seats.status}">${escapeHtml(labels[seats.status] || seats.status)}</span>`,
    `<span class="badge">${escapeHtml(labels[section.instruction_mode] || section.instruction_mode)}</span>`,
    ...section.requirement_tags.map((tag) => `<span class="badge">${escapeHtml(tag)}</span>`),
  ];

  const groupedSharedInfo = `
    <div class="shared-course-info">
      <p class="shared-course-title">Shared course information</p>
      <div class="schedule-grid grouped-shared-grid">
        <div><span class="data-label">Prerequisites</span><span class="data-value">${escapeHtml(section.prerequisite_text || "None listed")}</span></div>
        <div><span class="data-label">Campus</span><span class="data-value">${escapeHtml(section.campus)}</span></div>
        <div><span class="data-label">Term</span><span class="data-value">${escapeHtml(section.term)}</span></div>
      </div>
    </div>`;

  const singleGrid = `
    <div class="schedule-grid">
      <div><span class="data-label">Term</span><span class="data-value">${escapeHtml(section.term)}</span></div>
      <div><span class="data-label">Meetings</span><span class="data-value">${escapeHtml(meetingText(section.meetings))}</span></div>
      <div><span class="data-label">Seats</span><span class="data-value seat-value">${escapeHtml(seats.primary)}</span>${seatSecondary}${seatFreshness}</div>
      <div><span class="data-label">Campus</span><span class="data-value">${escapeHtml(section.campus)}</span></div>
      <div><span class="data-label">Prerequisites</span><span class="data-value">${escapeHtml(section.prerequisite_text || "None listed")}</span></div>
      <div><span class="data-label">Location</span><span class="data-value">${escapeHtml(location)}</span></div>
    </div>`;

  card.innerHTML = `
    <div>
      <div class="course-top">
        <div class="course-code-wrap">
          <p class="course-code">${escapeHtml(section.course_code)}</p>
          <button class="course-info" type="button" aria-label="Description for ${escapeHtml(section.course_code)}" aria-describedby="${tooltipId}">
            <span aria-hidden="true">i</span>
            <span id="${tooltipId}" class="course-tooltip" role="tooltip">${escapeHtml(description)}</span>
          </button>
          <button class="course-favorite" type="button" aria-label="Add ${escapeHtml(section.course_code)} ${escapeHtml(section.title)} to favorites" aria-pressed="false">
            <svg class="favorite-star-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path d="M12 2.75l2.77 5.61 6.19.9-4.48 4.37 1.06 6.17L12 16.88 6.46 19.8l1.06-6.17-4.48-4.37 6.19-.9L12 2.75z"></path>
            </svg>
          </button>
        </div>
        <div>
          <h3>${escapeHtml(section.title)}</h3>
          <p class="course-meta">${groupedMeta}</p>
        </div>
      </div>
      ${grouped ? `<div class="badges grouped-badges">${groupedTags.join("")}</div>${componentRows(section)}` : ""}
      ${grouped ? groupedSharedInfo : `<div class="badges">${singleTags.join("")}</div>${singleGrid}`}
    </div>
    ${grouped ? "" : professorPanel(section)}
  `;
  const favoriteButton = card.querySelector(".course-favorite");
  if (favoriteButton) {
    favoriteButton.dataset.favoriteKey = sectionFavoriteKey(section);
    setFavoriteButtonState(favoriteButton, section);
    favoriteButton.addEventListener("click", () => toggleFavorite(section));
  }
  return card;
}

function renderFavoritesPage() {
  const list = document.querySelector("#favorites-list");
  const empty = document.querySelector("#favorites-empty");
  if (!list || !empty) return;
  list.replaceChildren();
  const favorites = favoriteSections().sort((left, right) =>
    String(left.course_code).localeCompare(String(right.course_code), undefined, { numeric: true })
    || String(left.section_number || "").localeCompare(String(right.section_number || ""), undefined, { numeric: true })
  );
  empty.hidden = favorites.length > 0;
  favorites.forEach((section) => list.appendChild(courseCard(section)));
}

function adminNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toLocaleString() : "—";
}

function adminPercent(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toFixed(1)}%` : "—";
}

function adminDate(value) {
  if (!value) return "Not available";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString();
}

function adminBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let amount = bytes;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount >= 10 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
}

function adminMetric(label, value, { tone = "", note = "" } = {}) {
  return `<div class="admin-metric ${tone ? `admin-metric-${tone}` : ""}">
    <span class="admin-metric-label">${escapeHtml(label)}</span>
    <strong class="admin-metric-value">${escapeHtml(String(value))}</strong>
    ${note ? `<span class="admin-metric-note">${escapeHtml(note)}</span>` : ""}
  </div>`;
}

function renderAdminProfessorOverrides(professors) {
  const overrides = Array.isArray(professors.manual_overrides)
    ? professors.manual_overrides
    : [];
  const badge = document.querySelector("#admin-professor-override-count");
  if (badge) {
    badge.textContent = overrides.length
      ? `${adminNumber(overrides.length)} saved`
      : "None";
    badge.className = `admin-health-badge ${overrides.length ? "is-good" : ""}`;
  }

  const available = Boolean(professors.manual_matching_enabled)
    && !professors.manual_matching_error;
  const instructorInput = document.querySelector("#admin-professor-instructor-input");
  const profileInput = document.querySelector("#admin-professor-profile-input");
  const saveButton = document.querySelector("#admin-professor-override-save");
  [instructorInput, profileInput, saveButton].forEach((control) => {
    if (control) control.disabled = !available;
  });
  const result = document.querySelector("#admin-professor-override-result");
  if (!available && result) {
    result.textContent = professors.manual_matching_error
      ? "The professor ratings cache could not be read safely."
      : "Configure the professor ratings cache before adding manual matches.";
    result.className = "admin-seat-refresh-result is-error";
  }

  const table = document.querySelector("#admin-professor-overrides");
  if (!table) return;
  table.innerHTML = overrides.length
    ? overrides.map((record) => {
        const metrics = record.metrics || {};
        const profileUrl = metrics.profile_url || "";
        const profileLabel = record.rmp_name || `Professor ${metrics.external_id || ""}`;
        const profile = profileUrl
          ? `<a href="${escapeHtml(profileUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(profileLabel)} ↗</a>`
          : escapeHtml(profileLabel);
        const rating = metrics.rating == null ? "—" : Number(metrics.rating).toFixed(1);
        const difficulty = metrics.difficulty == null ? "—" : Number(metrics.difficulty).toFixed(1);
        const reviews = adminNumber(metrics.num_reviews || 0);
        return `<tr>
          <td><strong>${escapeHtml(record.name || "Unknown instructor")}</strong><span>${escapeHtml((record.course_codes || []).join(", ") || "No active course")}</span></td>
          <td>${profile}<span>${escapeHtml(record.rmp_department || "Department unavailable")}</span></td>
          <td>${escapeHtml(`${rating} quality · ${difficulty} difficulty · ${reviews} reviews`)}</td>
          <td>${escapeHtml(adminDate(record.updated_at || record.created_at))}</td>
          <td><div class="admin-professor-override-actions">
            <button class="admin-refresh" type="button" data-edit-professor="${escapeHtml(record.name || "")}" data-edit-profile="${escapeHtml(profileUrl || metrics.external_id || "")}">Change</button>
            <button class="admin-refresh admin-professor-override-remove" type="button" data-delete-professor="${escapeHtml(record.name || "")}">Remove</button>
          </div></td>
        </tr>`;
      }).join("")
    : '<tr><td class="admin-seat-failure-empty" colspan="5">No manual professor matches have been saved.</td></tr>';

  table.querySelectorAll("[data-edit-professor]").forEach((button) => {
    button.addEventListener("click", () => editAdminProfessorOverride(
      button.dataset.editProfessor,
      button.dataset.editProfile,
    ));
  });
  table.querySelectorAll("[data-delete-professor]").forEach((button) => {
    button.addEventListener("click", () => deleteAdminProfessorOverride(
      button.dataset.deleteProfessor,
      button,
    ));
  });
}

function renderAdminHealth(data) {
  state.adminHealth = data;
  const dashboard = document.querySelector("#admin-dashboard");
  if (!dashboard) return;
  dashboard.hidden = false;

  const passed = data?.status === "passed";
  const statusText = document.querySelector("#admin-status-text");
  const statusDot = document.querySelector("#admin-status-dot");
  const checkedAt = document.querySelector("#admin-checked-at");
  if (statusText) statusText.textContent = passed ? "Coverage audit passed" : "Coverage audit failed";
  if (statusDot) statusDot.className = `admin-status-dot ${passed ? "is-good" : "is-bad"}`;
  if (checkedAt) checkedAt.textContent = `Checked ${adminDate(data?.checked_at)}`;

  const seats = data?.seat_refresh || {};
  const seatMetrics = document.querySelector("#admin-seat-metrics");
  if (seatMetrics) {
    const staleMinutes = Number(seats.stale_after_seconds) / 60;
    const priorityMinutes = Number(seats.priority_interval_seconds) / 60;
    seatMetrics.innerHTML = [
      adminMetric("Service", seats.enabled ? (seats.running ? "Running" : "Stopped") : "Unavailable", { tone: seats.running ? "good" : "warning" }),
      adminMetric("Course pages", adminNumber(seats.refreshable_course_pages)),
      adminMetric("Cached sections", adminNumber(seats.cached_sections)),
      adminMetric("Fresh cached", adminNumber(seats.fresh_cached_sections), { tone: "good" }),
      adminMetric("Stale cached", adminNumber(seats.stale_cached_sections), { tone: seats.stale_cached_sections ? "warning" : "good" }),
      adminMetric("Priority pages", adminNumber(seats.priority_sources)),
      adminMetric("Successful refreshes", adminNumber(seats.successful_course_refreshes), { tone: "good" }),
      adminMetric("Failed refreshes", adminNumber(seats.failed_course_refreshes), { tone: seats.failed_course_refreshes ? "warning" : "good" }),
      adminMetric("Requests", adminNumber(seats.requests_completed)),
      adminMetric("Manual queue", adminNumber(seats.manual_refresh_pending)),
      adminMetric("Browser poll", `${adminNumber(seats.browser_poll_seconds)} sec`),
      adminMetric("Priority cadence", Number.isFinite(priorityMinutes) ? `${priorityMinutes.toFixed(priorityMinutes % 1 ? 1 : 0)} min` : "—"),
      adminMetric("Stale warning", Number.isFinite(staleMinutes) ? `${staleMinutes.toFixed(staleMinutes % 1 ? 1 : 0)} min` : "—"),
      adminMetric("Request delay", `${adminNumber(seats.request_delay_seconds)} sec`),
    ].join("");
  }
  const seatNote = document.querySelector("#admin-seat-note");
  if (seatNote) {
    const last = seats.last_success_at ? `Last success ${adminDate(seats.last_success_at)}` : "No live seat refresh completed yet";
    const cycle = seats.last_full_cycle_at ? ` · Last full sweep ${adminDate(seats.last_full_cycle_at)}` : "";
    const course = seats.last_course ? ` · Last course ${seats.last_course}` : "";
    const error = seats.last_error ? ` · Last error: ${seats.last_error}` : "";
    seatNote.textContent = `${last}${cycle}${course}${error}`;
  }
  const seatRefreshButton = document.querySelector("#admin-seat-refresh-now");
  if (seatRefreshButton) seatRefreshButton.disabled = !seats.enabled || !seats.running;
  const seatCourseInput = document.querySelector("#admin-seat-course-input");
  const seatCourseButton = document.querySelector("#admin-seat-refresh-course");
  const courseRefreshUnavailable = !seats.enabled || !seats.running;
  if (seatCourseInput) seatCourseInput.disabled = courseRefreshUnavailable;
  if (seatCourseButton) seatCourseButton.disabled = courseRefreshUnavailable;

  const seatFailures = Array.isArray(seats.seat_failures) ? seats.seat_failures : [];
  const unresolvedSeatFailures = seatFailures.filter((item) => !item.resolved).length;
  const seatFailureBadge = document.querySelector("#admin-seat-failure-count");
  if (seatFailureBadge) {
    seatFailureBadge.textContent = seatFailures.length
      ? `${adminNumber(unresolvedSeatFailures)} unresolved · ${adminNumber(seatFailures.length)} retained`
      : "None";
    seatFailureBadge.className = `admin-health-badge ${unresolvedSeatFailures ? "is-bad" : "is-good"}`;
  }
  const seatFailureList = document.querySelector("#admin-seat-failures");
  if (seatFailureList) {
    seatFailureList.innerHTML = seatFailures.length
      ? seatFailures.map((item) => {
          const courseCode = item.course_code || "Unknown course";
          const courseCodes = Array.isArray(item.course_codes) ? item.course_codes.join(", ") : courseCode;
          const sourceLink = item.source_url
            ? `<a href="${escapeHtml(item.source_url)}" target="_blank" rel="noopener noreferrer">Open SDSU source</a>`
            : "";
          const statusClass = item.resolved ? "is-recovered" : "is-unresolved";
          const statusLabel = item.resolved ? "Recovered" : "Unresolved";
          return `<tr>
            <td><div class="admin-seat-failure-course"><strong>${escapeHtml(courseCodes)}</strong>${sourceLink}</div></td>
            <td>${escapeHtml(adminDate(item.last_failed_at))}</td>
            <td class="admin-seat-failure-error"><strong>${escapeHtml(item.error_type || "Error")}</strong>: ${escapeHtml(item.detail || "No additional detail")}</td>
            <td>${escapeHtml(adminNumber(item.occurrences || 1))}</td>
            <td><span class="admin-seat-failure-status ${statusClass}">${statusLabel}</span></td>
            <td><button class="admin-refresh admin-seat-failure-retry" type="button" data-retry-course="${escapeHtml(courseCode)}" ${courseRefreshUnavailable ? "disabled" : ""}>Retry</button></td>
          </tr>`;
        }).join("")
      : '<tr><td class="admin-seat-failure-empty" colspan="6">No seat-refresh failures have been retained since this server started.</td></tr>';
    seatFailureList.querySelectorAll("[data-retry-course]").forEach((button) => {
      button.addEventListener("click", () => retrySeatFailureCourse(button.dataset.retryCourse, button));
    });
  }

  const coverage = data?.coverage || {};
  const coverageBadge = document.querySelector("#admin-coverage-badge");
  if (coverageBadge) {
    coverageBadge.textContent = passed ? "Passed" : "Failed";
    coverageBadge.className = `admin-health-badge ${passed ? "is-good" : "is-bad"}`;
  }
  const coverageMetrics = document.querySelector("#admin-coverage-metrics");
  if (coverageMetrics) {
    coverageMetrics.innerHTML = [
      adminMetric("Physical sections", adminNumber(coverage.physical_sections)),
      adminMetric("Accounted", adminNumber(coverage.accounted_physical_sections), { tone: coverage.unaccounted_physical_sections === 0 ? "good" : "" }),
      adminMetric("Unaccounted", adminNumber(coverage.unaccounted_physical_sections), { tone: coverage.unaccounted_physical_sections === 0 ? "good" : "bad" }),
      adminMetric("Displayed options", adminNumber(coverage.displayed_options)),
      adminMetric("Standalone physical", adminNumber(coverage.standalone_physical_sections)),
      adminMetric("Grouped physical", adminNumber(coverage.grouped_component_physical_sections)),
      adminMetric("Option memberships", adminNumber(coverage.physical_option_memberships)),
      adminMetric("Duplicate memberships", adminNumber(coverage.duplicate_option_memberships)),
    ].join("");
  }

  const unaccounted = document.querySelector("#admin-unaccounted");
  const examples = Array.isArray(coverage.unaccounted_examples) ? coverage.unaccounted_examples : [];
  if (unaccounted) {
    unaccounted.hidden = examples.length === 0;
    unaccounted.innerHTML = examples.length
      ? `<strong>Unaccounted examples</strong><ul>${examples.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
      : "";
  }

  const inventory = data?.inventory || {};
  const inventoryMetrics = document.querySelector("#admin-inventory-metrics");
  if (inventoryMetrics) {
    inventoryMetrics.innerHTML = [
      adminMetric("Course-section listings", adminNumber(inventory.course_section_listings)),
      adminMetric("Physical sections", adminNumber(inventory.physical_sections)),
      adminMetric("Enrollment options", adminNumber(inventory.displayed_options)),
      adminMetric("Course offerings", adminNumber(inventory.courses)),
      adminMetric("Subjects", adminNumber(inventory.subjects)),
      adminMetric("Terms", adminNumber(inventory.terms)),
    ].join("");
  }

  const professors = data?.professors || {};
  const rmpMetrics = document.querySelector("#admin-rmp-metrics");
  if (rmpMetrics) {
    rmpMetrics.innerHTML = [
      adminMetric("Named instructors", adminNumber(professors.instructors)),
      adminMetric("RMP matched", adminNumber(professors.matched), { tone: "good" }),
      adminMetric("RMP unmatched", adminNumber(professors.unmatched), { tone: professors.unmatched ? "warning" : "good" }),
      adminMetric("Match rate", adminPercent(professors.match_rate)),
      adminMetric("Cached records", adminNumber(professors.cached_rating_records)),
    ].join("");
  }
  const rmpProgress = document.querySelector("#admin-rmp-progress span");
  if (rmpProgress) {
    const rate = Math.max(0, Math.min(100, Number(professors.match_rate) || 0));
    rmpProgress.style.width = `${rate}%`;
  }
  const sync = professors.sync || {};
  const syncNote = document.querySelector("#admin-rmp-sync");
  if (syncNote) {
    syncNote.textContent = sync.generated_at
      ? `Last sync ${adminDate(sync.generated_at)} · ${adminNumber(sync.matched)} matched · ${adminNumber(sync.unmatched)} unmatched · ${adminNumber(sync.errors)} errors`
      : "No RateMyProfessors sync metadata available.";
  }
  renderAdminProfessorOverrides(professors);

  const completeness = data?.completeness || {};
  const completenessMetrics = document.querySelector("#admin-completeness-metrics");
  if (completenessMetrics) {
    const missingInstructor = Number(completeness.physical_sections_missing_instructor) || 0;
    const missingLocation = Number(completeness.physical_sections_missing_location) || 0;
    const missingMeetings = Number(completeness.physical_sections_missing_meetings) || 0;
    completenessMetrics.innerHTML = [
      adminMetric("Missing instructor", adminNumber(missingInstructor), { tone: missingInstructor ? "warning" : "good", note: "physical sections" }),
      adminMetric("Missing location", adminNumber(missingLocation), { tone: missingLocation ? "warning" : "good", note: "physical sections" }),
      adminMetric("No meeting rows", adminNumber(missingMeetings), { tone: missingMeetings ? "warning" : "good", note: "physical sections" }),
    ].join("");
  }

  const catalog = data?.catalog || {};
  const catalogMetrics = document.querySelector("#admin-catalog-metrics");
  if (catalogMetrics) {
    catalogMetrics.innerHTML = [
      adminMetric("Catalog status", catalog.loaded ? "Loaded" : "Not loaded", { tone: catalog.loaded ? "good" : "warning" }),
      adminMetric("Programs", adminNumber(catalog.programs)),
      adminMetric("Requirements", adminNumber(catalog.requirements)),
    ].join("");
  }

  const files = data?.files || {};
  const fileList = document.querySelector("#admin-files");
  if (fileList) {
    const labelsByKey = { sections: "Class sections", catalog: "Catalog mappings", ratings: "Professor ratings" };
    fileList.innerHTML = Object.entries(labelsByKey).map(([key, label]) => {
      const file = files[key] || {};
      return `<div class="admin-file-row">
        <div>
          <strong>${escapeHtml(label)}</strong>
          <span>${escapeHtml(file.name || "Not loaded")}</span>
        </div>
        <div class="admin-file-meta">
          <span>${file.loaded ? adminBytes(file.size_bytes) : "Unavailable"}</span>
          <span>${file.loaded ? `Updated ${escapeHtml(adminDate(file.modified_at))}` : ""}</span>
        </div>
      </div>`;
    }).join("");
  }

  const recentErrors = Array.isArray(data?.recent_errors) ? data.recent_errors : [];
  const recentErrorBadge = document.querySelector("#admin-recent-error-count");
  if (recentErrorBadge) {
    recentErrorBadge.textContent = recentErrors.length ? `${adminNumber(recentErrors.length)} retained` : "None";
    recentErrorBadge.className = `admin-health-badge ${recentErrors.length ? "is-bad" : "is-good"}`;
  }
  const recentErrorList = document.querySelector("#admin-recent-errors");
  if (recentErrorList) {
    recentErrorList.innerHTML = recentErrors.length
      ? recentErrors.map((item) => `<article class="admin-error-row">
          <div class="admin-error-heading">
            <strong>${escapeHtml(item.public_message || "Request failed")}</strong>
            <span>${escapeHtml(adminDate(item.occurred_at))}${Number(item.occurrences) > 1 ? ` · ${escapeHtml(adminNumber(item.occurrences))} occurrences` : ""}</span>
          </div>
          <div class="admin-error-meta">
            <span class="admin-error-status">${escapeHtml(String(item.status_code || 500))}</span>
            <code>${escapeHtml(`${item.method || "GET"} ${item.path || "Unknown path"}`)}</code>
            <span>${escapeHtml(item.code || "service_unavailable")}</span>
          </div>
          <p>${escapeHtml(`${item.exception_type || "Error"}: ${item.detail || "No additional detail"}`)}</p>
          <small>Reference ${escapeHtml(item.request_id || "not available")}</small>
        </article>`).join("")
      : '<p class="admin-errors-empty">No server errors have been recorded since this process started.</p>';
  }
}

async function refreshAllSeatsNow() {
  const button = document.querySelector("#admin-seat-refresh-now");
  if (button) button.disabled = true;
  try {
    await fetchJson("/api/admin/seats/refresh", { method: "POST", cache: "no-store" });
    await loadAdminHealth();
  } catch (err) {
    const error = document.querySelector("#admin-error");
    if (error) {
      error.textContent = safeErrorMessage(err, "Seat data is temporarily unavailable.");
      error.hidden = false;
    }
  } finally {
    if (button) button.disabled = false;
  }
}

function closeAdminSeatCourseSuggestions() {
  const box = document.querySelector("#admin-seat-course-suggestions");
  const input = document.querySelector("#admin-seat-course-input");
  if (!box || !input) return;
  box.hidden = true;
  box.replaceChildren();
  input.setAttribute("aria-expanded", "false");
  input.removeAttribute("aria-activedescendant");
  state.adminSeatCourseSuggestions = [];
  state.adminSeatCourseSuggestionIndex = -1;
}

function updateAdminSeatCourseSuggestionHighlight() {
  const box = document.querySelector("#admin-seat-course-suggestions");
  const input = document.querySelector("#admin-seat-course-input");
  if (!box || !input) return;
  [...box.querySelectorAll(".completed-course-suggestion")].forEach((button, index) => {
    const active = index === state.adminSeatCourseSuggestionIndex;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
    if (active) input.setAttribute("aria-activedescendant", button.id);
  });
  if (state.adminSeatCourseSuggestionIndex < 0) input.removeAttribute("aria-activedescendant");
}

function selectAdminSeatCourse(course) {
  const input = document.querySelector("#admin-seat-course-input");
  if (!input || !course) return;
  input.value = normalizeCourseCode(course.course_code);
  closeAdminSeatCourseSuggestions();
}

function renderAdminSeatCourseSuggestions() {
  const input = document.querySelector("#admin-seat-course-input");
  const box = document.querySelector("#admin-seat-course-suggestions");
  if (!input || !box) return;
  const matches = matchingAdminSeatCourseOptions(input.value);
  state.adminSeatCourseSuggestions = matches;
  state.adminSeatCourseSuggestionIndex = matches.length ? 0 : -1;
  box.replaceChildren();
  if (!matches.length) {
    closeAdminSeatCourseSuggestions();
    return;
  }
  matches.forEach((course, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "completed-course-suggestion";
    button.id = `admin-seat-course-option-${index}`;
    button.setAttribute("role", "option");
    const code = document.createElement("strong");
    code.textContent = course.course_code;
    const title = document.createElement("span");
    title.textContent = course.title || "Course title unavailable";
    button.append(code, title);
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => {
      selectAdminSeatCourse(course);
      input.focus();
    });
    box.appendChild(button);
  });
  box.hidden = false;
  input.setAttribute("aria-expanded", "true");
  updateAdminSeatCourseSuggestionHighlight();
}

function handleAdminSeatCourseKeydown(event) {
  const suggestions = state.adminSeatCourseSuggestions;
  if (event.key === "ArrowDown" && suggestions.length) {
    event.preventDefault();
    state.adminSeatCourseSuggestionIndex = (state.adminSeatCourseSuggestionIndex + 1) % suggestions.length;
    updateAdminSeatCourseSuggestionHighlight();
    return;
  }
  if (event.key === "ArrowUp" && suggestions.length) {
    event.preventDefault();
    state.adminSeatCourseSuggestionIndex = (state.adminSeatCourseSuggestionIndex - 1 + suggestions.length) % suggestions.length;
    updateAdminSeatCourseSuggestionHighlight();
    return;
  }
  if (event.key === "Escape") {
    closeAdminSeatCourseSuggestions();
    return;
  }
  if (event.key !== "Enter" || !suggestions.length) return;
  event.preventDefault();
  selectAdminSeatCourse(
    suggestions[state.adminSeatCourseSuggestionIndex] || suggestions[0],
  );
}

function setupAdminSeatCoursePicker() {
  const input = document.querySelector("#admin-seat-course-input");
  if (!input) return;
  input.addEventListener("input", renderAdminSeatCourseSuggestions);
  input.addEventListener("focus", renderAdminSeatCourseSuggestions);
  input.addEventListener("keydown", handleAdminSeatCourseKeydown);
  input.addEventListener("blur", () => window.setTimeout(closeAdminSeatCourseSuggestions, 120));
}

function closeAdminProfessorSuggestions() {
  const box = document.querySelector("#admin-professor-instructor-suggestions");
  const input = document.querySelector("#admin-professor-instructor-input");
  if (!box || !input) return;
  box.hidden = true;
  box.replaceChildren();
  input.setAttribute("aria-expanded", "false");
  input.removeAttribute("aria-activedescendant");
  state.adminProfessorSuggestions = [];
  state.adminProfessorSuggestionIndex = -1;
}

function updateAdminProfessorSuggestionHighlight() {
  const box = document.querySelector("#admin-professor-instructor-suggestions");
  const input = document.querySelector("#admin-professor-instructor-input");
  if (!box || !input) return;
  [...box.querySelectorAll(".completed-course-suggestion")].forEach((button, index) => {
    const active = index === state.adminProfessorSuggestionIndex;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
    if (active) input.setAttribute("aria-activedescendant", button.id);
  });
  if (state.adminProfessorSuggestionIndex < 0) {
    input.removeAttribute("aria-activedescendant");
  }
}

function selectAdminProfessor(item) {
  const input = document.querySelector("#admin-professor-instructor-input");
  if (!input || !item) return;
  input.value = item.name || "";
  closeAdminProfessorSuggestions();
}

function renderAdminProfessorSuggestions() {
  const input = document.querySelector("#admin-professor-instructor-input");
  const box = document.querySelector("#admin-professor-instructor-suggestions");
  if (!input || !box) return;
  const matches = matchingAdminProfessorOptions(input.value);
  state.adminProfessorSuggestions = matches;
  state.adminProfessorSuggestionIndex = matches.length ? 0 : -1;
  box.replaceChildren();
  if (!matches.length) {
    closeAdminProfessorSuggestions();
    return;
  }
  matches.forEach((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "completed-course-suggestion";
    button.id = `admin-professor-option-${index}`;
    button.setAttribute("role", "option");
    const name = document.createElement("strong");
    name.textContent = item.name || "Unknown instructor";
    const detail = document.createElement("span");
    const courses = (item.course_codes || []).join(", ") || "No active course";
    detail.textContent = item.manual_override ? `${courses} · Current manual match` : courses;
    button.append(name, detail);
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => {
      selectAdminProfessor(item);
      input.focus();
    });
    box.appendChild(button);
  });
  box.hidden = false;
  input.setAttribute("aria-expanded", "true");
  updateAdminProfessorSuggestionHighlight();
}

function handleAdminProfessorKeydown(event) {
  const suggestions = state.adminProfessorSuggestions;
  if (event.key === "ArrowDown" && suggestions.length) {
    event.preventDefault();
    state.adminProfessorSuggestionIndex = (state.adminProfessorSuggestionIndex + 1) % suggestions.length;
    updateAdminProfessorSuggestionHighlight();
    return;
  }
  if (event.key === "ArrowUp" && suggestions.length) {
    event.preventDefault();
    state.adminProfessorSuggestionIndex = (state.adminProfessorSuggestionIndex - 1 + suggestions.length) % suggestions.length;
    updateAdminProfessorSuggestionHighlight();
    return;
  }
  if (event.key === "Escape") {
    closeAdminProfessorSuggestions();
    return;
  }
  if (event.key !== "Enter" || !suggestions.length) return;
  event.preventDefault();
  selectAdminProfessor(
    suggestions[state.adminProfessorSuggestionIndex] || suggestions[0],
  );
}

function setupAdminProfessorPicker() {
  const input = document.querySelector("#admin-professor-instructor-input");
  if (!input) return;
  input.addEventListener("input", renderAdminProfessorSuggestions);
  input.addEventListener("focus", renderAdminProfessorSuggestions);
  input.addEventListener("keydown", handleAdminProfessorKeydown);
  input.addEventListener("blur", () => window.setTimeout(closeAdminProfessorSuggestions, 120));
}

function editAdminProfessorOverride(instructorName, profile) {
  const instructorInput = document.querySelector("#admin-professor-instructor-input");
  const profileInput = document.querySelector("#admin-professor-profile-input");
  if (!instructorInput || !profileInput) return;
  instructorInput.value = instructorName || "";
  profileInput.value = profile || "";
  closeAdminProfessorSuggestions();
  document.querySelector("#admin-professor-override-form")?.scrollIntoView({ behavior: "smooth", block: "center" });
  profileInput.focus();
  profileInput.select();
}

async function saveAdminProfessorOverride(event) {
  event.preventDefault();
  const instructorInput = document.querySelector("#admin-professor-instructor-input");
  const profileInput = document.querySelector("#admin-professor-profile-input");
  const button = document.querySelector("#admin-professor-override-save");
  const result = document.querySelector("#admin-professor-override-result");
  if (!instructorInput || !profileInput || !button) return;

  const selected = adminProfessorEditableOptions().find(
    (item) => normalizeProfessorName(item.name) === normalizeProfessorName(instructorInput.value),
  );
  if (!selected) {
    if (result) {
      result.textContent = "Select an unmatched instructor from the suggestions.";
      result.className = "admin-seat-refresh-result is-error";
    }
    return;
  }

  closeAdminProfessorSuggestions();
  button.disabled = true;
  if (result) {
    result.textContent = `Checking the RateMyProfessors profile for ${selected.name}…`;
    result.className = "admin-seat-refresh-result";
  }
  try {
    const payload = await fetchJson("/api/admin/professors/overrides", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        instructor_name: selected.name,
        rmp_profile: profileInput.value.trim(),
      }),
    }, { fallbackMessage: "Professor match could not be saved.", allowDetail: true });
    const record = payload.override || {};
    const metrics = record.metrics || {};
    if (result) {
      const rating = metrics.rating == null ? "no rating yet" : `${Number(metrics.rating).toFixed(1)}/5`;
      result.textContent = `Saved ${record.name || selected.name} → ${record.rmp_name || "RateMyProfessors profile"} (${rating}, ${adminNumber(metrics.num_reviews || 0)} reviews).`;
      result.className = "admin-seat-refresh-result is-success";
    }
    instructorInput.value = "";
    profileInput.value = "";
    await loadAdminHealth();
  } catch (err) {
    if (result) {
      result.textContent = safeErrorMessage(err, "Professor match could not be saved.");
      result.className = "admin-seat-refresh-result is-error";
    }
  } finally {
    button.disabled = false;
  }
}

async function deleteAdminProfessorOverride(instructorName, button) {
  if (!instructorName || !button) return;
  if (!window.confirm(`Remove the manual RateMyProfessors match for ${instructorName}?`)) return;
  const result = document.querySelector("#admin-professor-override-result");
  button.disabled = true;
  try {
    const params = new URLSearchParams({ instructor_name: instructorName });
    await fetchJson(`/api/admin/professors/overrides?${params.toString()}`, {
      method: "DELETE",
      cache: "no-store",
    }, { fallbackMessage: "Professor match could not be removed.", allowDetail: true });
    if (result) {
      result.textContent = `Removed the manual match for ${instructorName}.`;
      result.className = "admin-seat-refresh-result is-success";
    }
    await loadAdminHealth();
  } catch (err) {
    if (result) {
      result.textContent = safeErrorMessage(err, "Professor match could not be removed.");
      result.className = "admin-seat-refresh-result is-error";
    }
  } finally {
    if (button.isConnected) button.disabled = false;
  }
}

async function refreshSpecificCourseSeats(event) {
  event.preventDefault();
  const input = document.querySelector("#admin-seat-course-input");
  const button = document.querySelector("#admin-seat-refresh-course");
  const result = document.querySelector("#admin-seat-refresh-result");
  if (!input || !button) return;

  const selectedCourse = courseLookupByCode(input.value);
  if (!selectedCourse) {
    if (result) {
      result.textContent = "Select a course from the suggestions.";
      result.className = "admin-seat-refresh-result is-error";
    }
    return;
  }

  const courseCode = normalizeCourseCode(selectedCourse.course_code);
  closeAdminSeatCourseSuggestions();
  button.disabled = true;
  if (result) {
    result.textContent = `Queueing ${courseCode}…`;
    result.className = "admin-seat-refresh-result";
  }
  try {
    const payload = await fetchJson("/api/admin/seats/refresh/course", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course_code: courseCode }),
    }, { fallbackMessage: "Seat refresh could not be queued.", allowDetail: true });
    if (result) {
      const pages = Number(payload.queued_course_pages) || 1;
      result.textContent = `${payload.course_code} queued across ${pages} course page${pages === 1 ? "" : "s"}.`;
      result.className = "admin-seat-refresh-result is-success";
    }
    input.value = "";
    await loadAdminHealth();
  } catch (err) {
    if (result) {
      result.textContent = safeErrorMessage(err, "Seat refresh could not be queued.");
      result.className = "admin-seat-refresh-result is-error";
    }
  } finally {
    button.disabled = false;
  }
}

async function retrySeatFailureCourse(courseCode, button) {
  const normalizedCode = normalizeCourseCode(courseCode);
  const result = document.querySelector("#admin-seat-failure-result");
  if (!normalizedCode || !button) return;
  button.disabled = true;
  if (result) {
    result.textContent = `Queueing ${normalizedCode} for another seat refresh…`;
    result.className = "admin-seat-refresh-result";
  }
  try {
    const payload = await fetchJson("/api/admin/seats/refresh/course", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ course_code: normalizedCode }),
    }, { fallbackMessage: "Seat refresh could not be queued.", allowDetail: true });
    if (result) {
      const pages = Number(payload.queued_course_pages) || 1;
      result.textContent = `${payload.course_code} queued across ${pages} course page${pages === 1 ? "" : "s"}. Refresh Admin health after it runs to see whether it recovered.`;
      result.className = "admin-seat-refresh-result is-success";
    }
    await loadAdminHealth();
  } catch (err) {
    if (result) {
      result.textContent = safeErrorMessage(err, "Seat refresh could not be queued.");
      result.className = "admin-seat-refresh-result is-error";
    }
  } finally {
    button.disabled = false;
  }
}

function updateAdminAccessUi() {
  const adminNav = document.querySelector("#admin-nav");
  const authPanel = document.querySelector("#admin-auth-panel");
  const loginForm = document.querySelector("#admin-login-form");
  const disabled = document.querySelector("#admin-disabled");
  const refresh = document.querySelector("#admin-refresh");
  const logout = document.querySelector("#admin-logout");
  const dashboard = document.querySelector("#admin-dashboard");
  const loading = document.querySelector("#admin-loading");

  if (adminNav) adminNav.hidden = !state.adminAuthenticated;
  if (authPanel) authPanel.hidden = state.adminAuthenticated;
  if (loginForm) loginForm.hidden = !state.adminEnabled;
  if (disabled) disabled.hidden = state.adminEnabled;
  if (refresh) refresh.hidden = !state.adminAuthenticated;
  if (logout) logout.hidden = !state.adminAuthenticated;
  if (!state.adminAuthenticated) {
    if (dashboard) dashboard.hidden = true;
    if (loading) loading.hidden = true;
  }
}

async function loadAdminSession() {
  try {
    const session = await fetchJson("/api/admin/session", { cache: "no-store" });
    state.adminEnabled = Boolean(session.enabled);
    state.adminAuthenticated = Boolean(session.authenticated);
  } catch (_err) {
    state.adminEnabled = false;
    state.adminAuthenticated = false;
  }
  updateAdminAccessUi();
  if (window.location.hash === "#admin" && state.adminAuthenticated) await loadAdminHealth();
}

async function submitAdminLogin(event) {
  event.preventDefault();
  const password = document.querySelector("#admin-password");
  const submit = document.querySelector("#admin-login");
  const error = document.querySelector("#admin-login-error");
  if (!password || !submit) return;
  if (error) error.hidden = true;
  submit.disabled = true;
  try {
    await fetchJson("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: password.value }),
    }, { fallbackMessage: "Admin sign in failed.", allowDetail: true });
    password.value = "";
    state.adminEnabled = true;
    state.adminAuthenticated = true;
    updateAdminAccessUi();
    await loadAdminHealth();
  } catch (err) {
    if (error) {
      error.textContent = safeErrorMessage(err, "Admin sign in failed.");
      error.hidden = false;
    }
  } finally {
    submit.disabled = false;
  }
}

async function logoutAdmin() {
  try {
    await fetchJson("/api/admin/logout", { method: "POST", cache: "no-store" });
  } finally {
    state.adminAuthenticated = false;
    state.adminHealth = null;
    updateAdminAccessUi();
    window.location.hash = "";
  }
}

async function loadAdminHealth() {
  const loading = document.querySelector("#admin-loading");
  const error = document.querySelector("#admin-error");
  const refresh = document.querySelector("#admin-refresh");
  if (loading) loading.hidden = false;
  if (error) error.hidden = true;
  if (refresh) refresh.disabled = true;
  try {
    const payload = await fetchJson(
      "/api/admin/health",
      { cache: "no-store" },
      { fallbackMessage: "Admin data is temporarily unavailable.", allowDetail: true },
    );
    renderAdminHealth(payload);
  } catch (err) {
    if (err?.status === 401 || err?.status === 403 || err?.status === 404) {
      state.adminAuthenticated = false;
      updateAdminAccessUi();
    }
    if (error) {
      error.textContent = safeErrorMessage(err, "Admin data is temporarily unavailable.");
      error.hidden = false;
    }
  } finally {
    if (loading) loading.hidden = true;
    if (refresh) refresh.disabled = false;
  }
}

function syncPageFromHash() {
  const favoritesActive = window.location.hash === "#favorites";
  const adminActive = window.location.hash === "#admin";
  const aboutActive = window.location.hash === "#about";
  const browseActive = !favoritesActive && !adminActive && !aboutActive;
  const browsePage = document.querySelector("#browse-page");
  const favoritesPage = document.querySelector("#favorites-page");
  const adminPage = document.querySelector("#admin-page");
  const aboutPage = document.querySelector("#about-page");
  const favoritesNav = document.querySelector("#favorites-nav");
  const homeNav = document.querySelector("#home-nav");
  const adminNav = document.querySelector("#admin-nav");
  const aboutNav = document.querySelector("#about-nav");
  if (browsePage) browsePage.hidden = !browseActive;
  if (favoritesPage) favoritesPage.hidden = !favoritesActive;
  if (adminPage) adminPage.hidden = !adminActive;
  if (aboutPage) aboutPage.hidden = !aboutActive;
  if (favoritesNav) {
    favoritesNav.classList.toggle("is-active", favoritesActive);
    favoritesNav.setAttribute("aria-current", favoritesActive ? "page" : "false");
  }
  if (homeNav) {
    homeNav.classList.toggle("is-active", browseActive);
    homeNav.setAttribute("aria-current", browseActive ? "page" : "false");
  }
  if (adminNav) {
    adminNav.classList.toggle("is-active", adminActive);
    adminNav.setAttribute("aria-current", adminActive ? "page" : "false");
  }
  if (aboutNav) {
    aboutNav.classList.toggle("is-active", aboutActive);
    aboutNav.setAttribute("aria-current", aboutActive ? "page" : "false");
  }
  if (favoritesActive) {
    document.title = "Favorites — ClassCatalog";
    renderFavoritesPage();
  } else if (adminActive) {
    document.title = "Data Health — ClassCatalog";
    updateAdminAccessUi();
    if (state.adminAuthenticated) loadAdminHealth();
  } else if (aboutActive) {
    document.title = "About & Privacy — ClassCatalog";
  } else {
    document.title = "ClassCatalog — SDSU class finder";
  }
  window.scrollTo({ top: 0, behavior: "auto" });
}

function paginationTokens(currentPage, totalPages) {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  const pages = new Set([1, totalPages, currentPage - 1, currentPage, currentPage + 1]);
  if (currentPage <= 4) [2, 3, 4, 5].forEach((page) => pages.add(page));
  if (currentPage >= totalPages - 3) {
    [totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1].forEach((page) => pages.add(page));
  }

  const validPages = [...pages]
    .filter((page) => page >= 1 && page <= totalPages)
    .sort((left, right) => left - right);
  const tokens = [];
  validPages.forEach((page, index) => {
    if (index > 0 && page - validPages[index - 1] > 1) tokens.push("ellipsis");
    tokens.push(page);
  });
  return tokens;
}

const paginationViews = [
  {
    root: "#top-pagination",
    controls: "#top-page-controls",
    summary: "#top-showing-count",
    previous: "#top-previous-page",
    next: "#top-next-page",
    buttons: "#top-page-buttons",
    compact: true,
    hideWhenSingle: true,
  },
  {
    root: "#pagination",
    controls: "#page-controls",
    summary: "#showing-count",
    previous: "#previous-page",
    next: "#next-page",
    buttons: "#page-buttons",
    compact: false,
    hideWhenSingle: false,
  },
];

function setPaginationDisabled(disabled) {
  document.querySelectorAll(".page-button").forEach((button) => {
    const isBoundary = button.dataset.boundaryDisabled === "true";
    const isCurrentPage = button.getAttribute("aria-current") === "page";
    button.disabled = disabled || isBoundary || isCurrentPage;
  });
}

function goToPage(page) {
  if (page < 1 || page > state.totalPages || page === state.page) return;
  loadCourses({ page, scrollToResults: true });
}

function renderPaginationView(view, data, firstShown, lastShown) {
  const pagination = document.querySelector(view.root);
  const pageControls = document.querySelector(view.controls);
  const showingCount = document.querySelector(view.summary);
  const previous = document.querySelector(view.previous);
  const next = document.querySelector(view.next);
  const pageButtons = document.querySelector(view.buttons);

  pagination.hidden = data.filtered_total === 0 || (view.hideWhenSingle && data.total_pages <= 1);
  if (pagination.hidden) return;

  showingCount.textContent = view.compact
    ? `Page ${data.page.toLocaleString()} of ${data.total_pages.toLocaleString()} · ${firstShown.toLocaleString()}–${lastShown.toLocaleString()} of ${data.filtered_total.toLocaleString()}`
    : `Showing ${firstShown.toLocaleString()}–${lastShown.toLocaleString()} of ${data.filtered_total.toLocaleString()} classes · Page ${data.page.toLocaleString()} of ${data.total_pages.toLocaleString()}`;

  pageControls.hidden = data.total_pages <= 1;
  previous.dataset.boundaryDisabled = String(data.page <= 1);
  next.dataset.boundaryDisabled = String(data.page >= data.total_pages);
  previous.disabled = data.page <= 1;
  next.disabled = data.page >= data.total_pages;
  pageButtons.replaceChildren();

  paginationTokens(data.page, data.total_pages).forEach((token) => {
    if (token === "ellipsis") {
      const ellipsis = document.createElement("span");
      ellipsis.className = "page-ellipsis";
      ellipsis.textContent = "…";
      ellipsis.setAttribute("aria-hidden", "true");
      pageButtons.appendChild(ellipsis);
      return;
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "page-button page-number";
    button.textContent = String(token);
    button.setAttribute("aria-label", `Go to page ${token}`);
    if (token === data.page) {
      button.classList.add("current");
      button.setAttribute("aria-current", "page");
      button.disabled = true;
    } else {
      button.addEventListener("click", () => goToPage(token));
    }
    pageButtons.appendChild(button);
  });
}

function renderPagination(data) {
  state.page = data.page;
  state.totalPages = data.total_pages;
  if (data.filtered_total === 0) {
    paginationViews.forEach((view) => {
      document.querySelector(view.root).hidden = true;
    });
    return;
  }

  const firstShown = (data.page - 1) * data.page_size + 1;
  const lastShown = Math.min(data.page * data.page_size, data.filtered_total);
  paginationViews.forEach((view) => renderPaginationView(view, data, firstShown, lastShown));
}

function renderCourses(data) {
  const list = document.querySelector("#course-list");
  list.replaceChildren();
  document.querySelector("#result-count").textContent = data.filtered_total.toLocaleString();
  document.querySelector("#result-context").textContent = data.filtered_total === data.unfiltered_total ? "classes available" : `of ${data.unfiltered_total.toLocaleString()} classes`;

  if (!data.items.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.innerHTML = "<strong>No classes match.</strong><br>Remove one or two filters and try again.";
    list.appendChild(empty);
    renderPagination(data);
    return;
  }

  data.items.forEach((section) => list.appendChild(courseCard(section)));
  renderPagination(data);
}

function renderActiveFilters(params) {
  const container = document.querySelector("#active-filters");
  container.replaceChildren();
  const hiddenKeys = ["page", "page_size", "sort_by"];
  const entries = [...params.entries()].filter(([key]) => !hiddenKeys.includes(key));
  let completedCoursesChipAdded = false;

  entries.forEach(([key, value]) => {
    if (key === "completed_course") {
      if (completedCoursesChipAdded) return;
      completedCoursesChipAdded = true;
      const chip = document.createElement("span");
      chip.className = "filter-chip";
      chip.textContent = "completed courses";
      container.appendChild(chip);
      return;
    }

    const chip = document.createElement("span");
    chip.className = "filter-chip";
    const displayKey = key === "exclude_day" ? "exclude day" : key.replaceAll("_", " ");
    chip.textContent = `${displayKey}: ${labels[value] || dayLabels[value] || value}`;
    container.appendChild(chip);
  });
}

async function loadCourses({ page = 1, scrollToResults = false, quiet = false } = {}) {
  if (quiet && state.controller) return;
  if (state.controller) state.controller.abort();
  const controller = new AbortController();
  state.controller = controller;

  const loading = document.querySelector("#loading");
  const error = document.querySelector("#error");
  if (!quiet) loading.hidden = false;
  error.hidden = true;
  if (!quiet) setPaginationDisabled(true);

  const params = buildParams(page);
  renderActiveFilters(params);
  try {
    const data = await fetchJson(`/api/classes?${params.toString()}`, { signal: controller.signal });
    renderCourses(data);
    if (scrollToResults) {
      window.requestAnimationFrame(() => {
        const results = document.querySelector(".results");
        const toolbar = document.querySelector(".toolbar");
        const offset = window.matchMedia("(min-width: 761px)").matches
          ? (toolbar?.getBoundingClientRect().height || 0) + 16
          : 12;
        const top = results.getBoundingClientRect().top + window.scrollY - offset;
        window.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
      });
    }
  } catch (err) {
    if (err?.name !== "AbortError") {
      error.textContent = safeErrorMessage(err, "Class results are temporarily unavailable. Please try again.");
      error.hidden = false;
    }
  } finally {
    if (state.controller === controller) {
      state.controller = null;
      if (!quiet) {
        loading.hidden = true;
        setPaginationDisabled(false);
      }
    }
  }
}

function scheduleLoad() {
  window.clearTimeout(state.debounceTimer);
  state.debounceTimer = window.setTimeout(() => loadCourses({ page: 1 }), 140);
}

function ensureTimeResetButtons() {
  const definitions = [
    { targetId: "time-from", label: "Starts after" },
    { targetId: "time-to", label: "Ends before" },
  ];

  definitions.forEach(({ targetId, label }) => {
    const input = document.querySelector(`#${targetId}`);
    if (!input) return;
    let control = input.closest(".time-filter-control");
    if (!control) {
      const parentLabel = input.closest("label");
      control = document.createElement("div");
      control.className = "time-filter-control";
      if (parentLabel) {
        parentLabel.parentNode.insertBefore(control, parentLabel);
        control.appendChild(parentLabel);
      } else {
        input.parentNode.insertBefore(control, input);
        control.appendChild(input);
      }
    }

    let row = control.querySelector(".time-filter-label-row");
    if (!row) {
      row = document.createElement("div");
      row.className = "time-filter-label-row";
      const existingLabel = control.querySelector("label");
      if (existingLabel) {
        const nestedInput = existingLabel.querySelector(`#${targetId}`);
        if (nestedInput) control.appendChild(nestedInput);
        existingLabel.htmlFor = targetId;
        if (!existingLabel.textContent.trim()) existingLabel.textContent = label;
        row.appendChild(existingLabel);
      } else {
        const createdLabel = document.createElement("label");
        createdLabel.htmlFor = targetId;
        createdLabel.textContent = label;
        row.appendChild(createdLabel);
      }
      control.insertBefore(row, control.firstChild);
    }

    let button = control.querySelector(`.time-reset[data-time-target="${targetId}"]`);
    if (!button) {
      button = document.createElement("button");
      button.className = "time-reset";
      button.type = "button";
      button.dataset.timeTarget = targetId;
      button.setAttribute("aria-label", `Reset ${label} to any time`);
      button.textContent = "Reset";
      row.appendChild(button);
    }
  });
}

function resetTimeFilter(targetId) {
  const input = document.querySelector(`#${targetId}`);
  if (!input) return;
  input.value = "";
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

function syncStickyFilterOffset() {
  const toolbar = document.querySelector(".toolbar");
  if (!toolbar) return;
  const toolbarHeight = Math.ceil(toolbar.getBoundingClientRect().height);
  document.documentElement.style.setProperty("--toolbar-sticky-height", `${toolbarHeight}px`);
}

function setupFilterFlyouts() {
  const filters = document.querySelector(".filters");
  if (!filters) return;

  const groups = [...filters.querySelectorAll(":scope > details")];
  if (!groups.length) return;

  const sideFlyoutBreakpoint = 900;
  let animationFrame = 0;

  const openGroup = () => groups.find((group) => group.open);

  function closeOtherGroups(activeGroup) {
    groups.forEach((group) => {
      if (group !== activeGroup && group.open) group.open = false;
    });
  }

  function syncFlyoutMode() {
    const useSideFlyout = window.innerWidth > sideFlyoutBreakpoint;
    filters.classList.toggle("filters-side-flyout", useSideFlyout);

    if (!useSideFlyout) {
      filters.style.setProperty("--filter-flyout-x-shift", "0px");
      filters.style.setProperty("--filter-flyout-y-shift", "0px");
    }

    positionOpenFlyout();
  }

  function positionOpenFlyout() {
    const group = openGroup();
    if (!group || !filters.classList.contains("filters-side-flyout")) {
      filters.style.setProperty("--filter-flyout-x-shift", "0px");
      filters.style.setProperty("--filter-flyout-y-shift", "0px");
      return;
    }

    const body = group.querySelector(":scope > .filter-body");
    if (!body) return;

    filters.style.setProperty("--filter-flyout-x-shift", "0px");
    filters.style.setProperty("--filter-flyout-y-shift", "0px");

    const rect = body.getBoundingClientRect();
    const edge = 12;
    let xShift = 0;
    let yShift = 0;

    // Prefer extending left, but never let the popout be clipped by the
    // viewport. If the filter rail is close to the left edge, this naturally
    // makes more of the popout overlap the rail, like a native select menu.
    if (rect.left < edge) xShift += edge - rect.left;
    if (rect.right + xShift > window.innerWidth - edge) {
      xShift -= rect.right + xShift - (window.innerWidth - edge);
    }

    const bottomLimit = window.innerHeight - edge;
    if (rect.bottom > bottomLimit) yShift -= rect.bottom - bottomLimit;
    if (rect.top + yShift < edge) yShift += edge - (rect.top + yShift);

    filters.style.setProperty("--filter-flyout-x-shift", `${Math.round(xShift)}px`);
    filters.style.setProperty("--filter-flyout-y-shift", `${Math.round(yShift)}px`);
  }

  function requestFlyoutPosition() {
    if (animationFrame) return;
    animationFrame = window.requestAnimationFrame(() => {
      animationFrame = 0;
      positionOpenFlyout();
    });
  }

  groups.forEach((group) => {
    group.open = false;
    group.addEventListener("toggle", () => {
      if (group.open) closeOtherGroups(group);
      requestFlyoutPosition();
    });
  });

  document.addEventListener("pointerdown", (event) => {
    if (!filters.classList.contains("filters-side-flyout")) return;
    if (filters.contains(event.target)) return;
    const group = openGroup();
    if (group) group.open = false;
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const group = openGroup();
    if (!group) return;
    const summary = group.querySelector(":scope > summary");
    group.open = false;
    summary?.focus();
  });

  window.addEventListener("resize", syncFlyoutMode);
  window.addEventListener("scroll", requestFlyoutPosition, { passive: true });

  if ("ResizeObserver" in window) {
    const resizeObserver = new ResizeObserver(requestFlyoutPosition);
    groups.forEach((group) => {
      const body = group.querySelector(":scope > .filter-body");
      if (body) resizeObserver.observe(body);
    });
  }

  syncFlyoutMode();
}

function clearFilters() {
  document.querySelectorAll('.filters input[type="checkbox"]').forEach((input) => { input.checked = false; });
  document.querySelectorAll('#days .day-toggle').forEach((button) => {
    button.dataset.state = "off";
    const label = button.textContent.trim();
    button.setAttribute("aria-label", `${label}: not filtered. Click to include.`);
  });
  const defaultCampus = document.querySelector('input[name="campus"][value="San Diego Campus"]');
  if (defaultCampus) defaultCampus.checked = true;
  document.querySelectorAll('.filters input:not([type="checkbox"]), .filters select').forEach((control) => { control.value = ""; });
  persistMajorProgram("");
  persistCompletedCourses("");
  renderCompletedCourseChips();
  closeCompletedCourseSuggestions();
  syncClassificationAvailability();
  hideProgramSummary();
  scheduleLoad();
}

async function boot() {
  setupThemePicker();
  updateFavoritesCount();
  setupFilterFlyouts();
  syncPageFromHash();
  window.addEventListener("hashchange", syncPageFromHash);
  const adminRefresh = document.querySelector("#admin-refresh");
  if (adminRefresh) adminRefresh.addEventListener("click", loadAdminHealth);
  const adminSeatRefresh = document.querySelector("#admin-seat-refresh-now");
  if (adminSeatRefresh) adminSeatRefresh.addEventListener("click", refreshAllSeatsNow);
  const adminSeatRefreshForm = document.querySelector("#admin-seat-refresh-form");
  if (adminSeatRefreshForm) adminSeatRefreshForm.addEventListener("submit", refreshSpecificCourseSeats);
  setupAdminSeatCoursePicker();
  const adminProfessorOverrideForm = document.querySelector("#admin-professor-override-form");
  if (adminProfessorOverrideForm) adminProfessorOverrideForm.addEventListener("submit", saveAdminProfessorOverride);
  setupAdminProfessorPicker();
  const adminLoginForm = document.querySelector("#admin-login-form");
  if (adminLoginForm) adminLoginForm.addEventListener("submit", submitAdminLogin);
  const adminLogout = document.querySelector("#admin-logout");
  if (adminLogout) adminLogout.addEventListener("click", logoutAdmin);
  await loadAdminSession();
  const [options, catalogStatus, ratingsStatus] = await Promise.all([
    fetchJson("/api/options"),
    fetchJson("/api/catalog/status").catch((error) => {
      setServiceMessage("catalog-status", safeErrorMessage(error, "Catalog information is temporarily unavailable."));
      return { loaded: false, catalog_years: [], programs: 0, requirements: 0 };
    }),
    fetchJson("/api/ratings/status", { cache: "no-store" }).catch((error) => {
      setServiceMessage("ratings-status", safeErrorMessage(error, "Professor ratings could not be loaded."));
      return null;
    }),
  ]);
  populateOptions(options);
  restoreCompletedCourses();
  renderCatalogStatus(catalogStatus);
  if (ratingsStatus) {
    setServiceMessage(
      "ratings-status",
      ratingsStatus.available ? "" : "Professor ratings are currently unavailable.",
    );
  }
  ensureTimeResetButtons();

  document.querySelectorAll("input, select").forEach((control) => {
    if (control.hasAttribute("data-no-search")) return;
    const eventName = control.type === "search" || control.type === "text" || control.type === "number" ? "input" : "change";
    control.addEventListener(eventName, scheduleLoad);
  });
  document.querySelector("#clear-filters").addEventListener("click", clearFilters);
  document.querySelector("#program").addEventListener("change", () => {
    const selectedProgram = document.querySelector("#program").value;
    persistMajorProgram(selectedProgram);
    const year = document.querySelector("#catalog-year");
    if (selectedProgram && !year.value && state.options.catalog_years.length === 1) {
      year.value = state.options.catalog_years[0];
    }
    syncClassificationAvailability();
    scheduleProgramSummary();
  });
  document.querySelector("#catalog-year").addEventListener("change", () => {
    syncClassificationAvailability();
    scheduleProgramSummary();
  });
  setupCompletedCoursePicker();
  document.querySelectorAll(".time-reset").forEach((button) => {
    button.addEventListener("click", () => resetTimeFilter(button.dataset.timeTarget));
  });

  syncStickyFilterOffset();
  window.addEventListener("resize", syncStickyFilterOffset);
  if ("ResizeObserver" in window) {
    new ResizeObserver(syncStickyFilterOffset).observe(document.querySelector(".toolbar"));
  }

  document.querySelector("#top-previous-page").addEventListener("click", () => goToPage(state.page - 1));
  document.querySelector("#top-next-page").addEventListener("click", () => goToPage(state.page + 1));
  document.querySelector("#previous-page").addEventListener("click", () => goToPage(state.page - 1));
  document.querySelector("#next-page").addEventListener("click", () => goToPage(state.page + 1));
  await loadCourses({ page: 1 });
  await loadSeatRefreshStatus();
  startSeatPolling();
}

window.addEventListener("error", () => {
  setServiceMessage("unexpected-browser-error", "Something went wrong. Please try again.");
});

window.addEventListener("unhandledrejection", (event) => {
  if (event.reason?.name === "AbortError") return;
  setServiceMessage(
    "unexpected-browser-error",
    safeErrorMessage(event.reason, "Something went wrong. Please try again."),
  );
});

boot().catch((err) => {
  const error = document.querySelector("#error");
  error.textContent = safeErrorMessage(err, "ClassCatalog could not finish loading. Please try again.");
  error.hidden = false;
});
