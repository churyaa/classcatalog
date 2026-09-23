const scheduleStorageKey = "classcatalog_schedule_v1";
const scheduleWeekdayOrder = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const scheduleDefaultStartMinutes = 7 * 60;
const scheduleDefaultEndMinutes = 22 * 60;

function readSchedule() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(scheduleStorageKey) || "{}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function writeSchedule(schedule) {
  try {
    window.localStorage.setItem(scheduleStorageKey, JSON.stringify(schedule));
  } catch {
    // The schedule is a local convenience feature; browsing should still work if storage is unavailable.
  }
}

function scheduleSectionKey(section) {
  return sectionFavoriteKey(section);
}

function scheduledSections(term = state.selectedTerm) {
  return Object.values(readSchedule())
    .filter((section) => section && section.course_code && section.schedule_number)
    .filter((section) => !term || String(section.term || "").trim() === term);
}

function isSectionScheduled(section) {
  return Boolean(readSchedule()[scheduleSectionKey(section)]);
}

function schedulePhysicalComponents(section) {
  const linked = Array.isArray(section.linked_components) ? section.linked_components : [];
  if (linked.length > 1) {
    return linked.map((component) => ({
      ...section,
      ...component,
      term: section.term,
      course_code: section.course_code,
      title: section.title,
      linked_components: [],
    }));
  }
  return [{ ...section, linked_components: [] }];
}

function timeToMinutes(value) {
  if (!value) return null;
  const parts = String(value).split(":");
  if (parts.length < 2) return null;
  const hour = Number(parts[0]);
  const minute = Number(parts[1]);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
  return hour * 60 + minute;
}

function parseMeetingDateRange(value) {
  const matches = String(value || "").match(/\b\d{1,2}\/\d{1,2}\/\d{4}\b/g) || [];
  if (matches.length < 2) return null;
  const parseDate = (raw) => {
    const [month, day, year] = raw.split("/").map(Number);
    const timestamp = Date.UTC(year, month - 1, day);
    return Number.isFinite(timestamp) ? timestamp : null;
  };
  const start = parseDate(matches[0]);
  const end = parseDate(matches[1]);
  return start == null || end == null ? null : { start, end };
}

function meetingDateRangesOverlap(left, right) {
  const leftRange = parseMeetingDateRange(left.meeting_dates);
  const rightRange = parseMeetingDateRange(right.meeting_dates);
  if (!leftRange || !rightRange) return true;
  return leftRange.start <= rightRange.end && rightRange.start <= leftRange.end;
}

function meetingsOverlap(left, right) {
  const leftStart = timeToMinutes(left.start_time);
  const leftEnd = timeToMinutes(left.end_time);
  const rightStart = timeToMinutes(right.start_time);
  const rightEnd = timeToMinutes(right.end_time);
  if ([leftStart, leftEnd, rightStart, rightEnd].some((value) => value == null)) return false;
  if (!(leftStart < rightEnd && rightStart < leftEnd)) return false;

  const rightDays = new Set((right.days || []).map(String));
  const sharesDay = (left.days || []).some((day) => rightDays.has(String(day)));
  return sharesDay && meetingDateRangesOverlap(left, right);
}

function sectionHasOverlap(candidate, scheduled) {
  const candidateMeetings = schedulePhysicalComponents(candidate)
    .flatMap((component) => component.meetings || []);
  const scheduledMeetings = schedulePhysicalComponents(scheduled)
    .flatMap((component) => component.meetings || []);
  return candidateMeetings.some((left) => scheduledMeetings.some((right) => meetingsOverlap(left, right)));
}

function findScheduleConflict(section) {
  const key = scheduleSectionKey(section);
  return scheduledSections(String(section.term || "").trim())
    .find((scheduled) => scheduleSectionKey(scheduled) !== key && sectionHasOverlap(section, scheduled)) || null;
}

function updateScheduleCount() {
  const count = scheduledSections().length;
  const badge = document.querySelector("#schedule-count");
  if (badge) badge.textContent = count.toLocaleString();
  const nav = document.querySelector("#schedule-nav");
  if (nav) {
    nav.setAttribute("aria-label", `Schedule${count ? `, ${count} class${count === 1 ? "" : "es"}` : ""}`);
    nav.title = count ? `Schedule (${count})` : "Schedule";
  }
}

function syncScheduleButton(button, section) {
  if (!button || !section) return;
  button._classCatalogSection = section;
  button.classList.remove("is-scheduled", "has-overlap");
  button.removeAttribute("title");

  if (isSectionScheduled(section)) {
    button.textContent = "In Schedule";
    button.setAttribute("aria-label", `${section.course_code} is already in Schedule`);
    button.disabled = true;
    button.classList.add("is-scheduled");
    return;
  }

  const conflict = findScheduleConflict(section);
  if (conflict) {
    button.textContent = `Overlaps with ${conflict.course_code}`;
    button.setAttribute("aria-label", `${section.course_code} overlaps with ${conflict.course_code} and cannot be added`);
    button.disabled = true;
    button.classList.add("has-overlap");
    button.title = `${section.course_code} overlaps with ${conflict.course_code}`;
    return;
  }

  button.textContent = "Add to Schedule";
  button.setAttribute("aria-label", `Add ${section.course_code} to Schedule`);
  button.disabled = false;
}

function syncScheduleButtons() {
  document.querySelectorAll(".schedule-add-button").forEach((button) => {
    syncScheduleButton(button, button._classCatalogSection);
  });
}

function scheduleChanged() {
  updateScheduleCount();
  syncScheduleButtons();
  if (state.scheduleDrawerOpen) renderScheduleDrawer();
}

function addScheduleSection(section) {
  if (!section || isSectionScheduled(section) || findScheduleConflict(section)) return;
  const schedule = readSchedule();
  schedule[scheduleSectionKey(section)] = section;
  writeSchedule(schedule);
  scheduleChanged();
}

function removeScheduleSection(key) {
  const schedule = readSchedule();
  if (!schedule[key]) return;
  delete schedule[key];
  writeSchedule(schedule);
  if (state.scheduleDetailKey === key) state.scheduleDetailKey = null;
  scheduleChanged();
}

function scheduleActionButtonMarkup() {
  return '<button class="schedule-add-button" type="button">Add to Schedule</button>';
}

function wireScheduleButtons(root, section) {
  root.querySelectorAll(".schedule-add-button").forEach((button) => {
    button._classCatalogSection = section;
    button.addEventListener("click", () => addScheduleSection(section));
    syncScheduleButton(button, section);
  });
}

function scheduleScheduleNumbers() {
  return [...new Set(scheduledSections().flatMap((section) => [
    section.schedule_number,
    ...(section.linked_components || []).map((component) => component.schedule_number),
  ]).map((value) => String(value || "").trim()).filter(Boolean))];
}

async function refreshScheduleSeats() {
  const schedules = scheduleScheduleNumbers();
  if (!schedules.length) return;
  const params = new URLSearchParams();
  schedules.slice(0, 250).forEach((schedule) => params.append("schedule_number", schedule));
  try {
    const payload = await fetchJson(`/api/seats?${params.toString()}`, { cache: "no-store" });
    const records = payload.records || {};
    const schedule = readSchedule();
    Object.entries(schedule).forEach(([key, section]) => {
      schedule[key] = applySeatRecordsToFavorite(section, records);
    });
    writeSchedule(schedule);
    setServiceMessage("schedule-seats");
    if (state.scheduleDrawerOpen) renderScheduleDrawer();
  } catch (error) {
    setServiceMessage("schedule-seats", safeErrorMessage(error, "Scheduled class seat data is temporarily unavailable."));
  }
}

function scheduleMeetingEntries(sections) {
  const timed = [];
  const unscheduled = [];

  sections.forEach((section) => {
    const key = scheduleSectionKey(section);
    schedulePhysicalComponents(section).forEach((component) => {
      const meetings = Array.isArray(component.meetings) ? component.meetings : [];
      const timedMeetings = meetings.filter((meeting) => (
        (meeting.days || []).length
        && timeToMinutes(meeting.start_time) != null
        && timeToMinutes(meeting.end_time) != null
      ));

      timedMeetings.forEach((meeting) => {
        (meeting.days || []).forEach((day) => {
          timed.push({ section, component, meeting, day: String(day), key });
        });
      });

      if (!timedMeetings.length) {
        unscheduled.push({ section, component, key });
      }
    });
  });

  return { timed, unscheduled };
}

function scheduleCourseTint(courseCode) {
  let hash = 0;
  String(courseCode || "").split("").forEach((character) => {
    hash = ((hash << 5) - hash + character.charCodeAt(0)) | 0;
  });
  return `${48 + Math.abs(hash % 28)}%`;
}

function renderScheduleCalendar(sections) {
  const calendar = document.querySelector("#schedule-calendar");
  const empty = document.querySelector("#schedule-empty");
  const unscheduledContainer = document.querySelector("#schedule-unscheduled");
  if (!calendar || !empty || !unscheduledContainer) return;

  calendar.replaceChildren();
  unscheduledContainer.replaceChildren();
  empty.hidden = sections.length > 0;
  calendar.hidden = sections.length === 0;

  if (!sections.length) {
    unscheduledContainer.hidden = true;
    return;
  }

  const { timed, unscheduled } = scheduleMeetingEntries(sections);
  const usedDays = new Set(timed.map((entry) => entry.day));
  const visibleDays = scheduleWeekdayOrder.filter((day, index) => index < 5 || usedDays.has(day));
  const startValues = timed.map((entry) => timeToMinutes(entry.meeting.start_time)).filter((value) => value != null);
  const endValues = timed.map((entry) => timeToMinutes(entry.meeting.end_time)).filter((value) => value != null);
  const startMinutes = startValues.length
    ? Math.min(scheduleDefaultStartMinutes, Math.floor(Math.min(...startValues) / 60) * 60)
    : scheduleDefaultStartMinutes;
  const endMinutes = endValues.length
    ? Math.max(scheduleDefaultEndMinutes, Math.ceil(Math.max(...endValues) / 60) * 60)
    : scheduleDefaultEndMinutes;
  const calendarHeight = Math.max(60, endMinutes - startMinutes);

  const week = document.createElement("div");
  week.className = "schedule-calendar-week";
  week.style.setProperty("--schedule-day-count", String(visibleDays.length));
  week.style.setProperty("--schedule-calendar-height", `${calendarHeight}px`);

  const corner = document.createElement("div");
  corner.className = "schedule-calendar-corner";
  week.appendChild(corner);

  visibleDays.forEach((day) => {
    const header = document.createElement("div");
    header.className = "schedule-day-header";
    header.textContent = dayLabels[day] || day;
    week.appendChild(header);
  });

  const axis = document.createElement("div");
  axis.className = "schedule-time-axis";
  axis.style.height = `${calendarHeight}px`;
  for (let minute = startMinutes; minute < endMinutes; minute += 60) {
    const label = document.createElement("span");
    label.className = "schedule-time-label";
    label.style.top = `${minute - startMinutes}px`;
    label.textContent = formatTime(`${String(Math.floor(minute / 60)).padStart(2, "0")}:00`);
    axis.appendChild(label);
  }
  week.appendChild(axis);

  visibleDays.forEach((day) => {
    const column = document.createElement("div");
    column.className = "schedule-day-column";
    column.style.height = `${calendarHeight}px`;
    timed.filter((entry) => entry.day === day).forEach((entry) => {
      const start = timeToMinutes(entry.meeting.start_time);
      const end = timeToMinutes(entry.meeting.end_time);
      const block = document.createElement("button");
      block.type = "button";
      block.className = "schedule-calendar-block";
      block.style.top = `${Math.max(0, start - startMinutes)}px`;
      block.style.height = `${Math.max(28, end - start)}px`;
      block.style.setProperty("--schedule-tint", scheduleCourseTint(entry.section.course_code));
      const componentLabel = entry.component.component || "";
      block.innerHTML = `<strong>${escapeHtml(entry.section.course_code)}</strong>${componentLabel ? `<span>${escapeHtml(componentLabel)}</span>` : ""}<small>${escapeHtml(formatTime(entry.meeting.start_time))}–${escapeHtml(formatTime(entry.meeting.end_time))}</small>`;
      block.setAttribute("aria-label", `Open ${entry.section.course_code} schedule details`);
      block.addEventListener("click", () => openScheduleDetail(entry.key));
      column.appendChild(block);
    });
    week.appendChild(column);
  });

  calendar.appendChild(week);

  unscheduledContainer.hidden = unscheduled.length === 0;
  if (unscheduled.length) {
    const heading = document.createElement("h3");
    heading.textContent = "Asynchronous / TBA";
    unscheduledContainer.appendChild(heading);
    const list = document.createElement("div");
    list.className = "schedule-unscheduled-list";
    unscheduled.forEach((entry) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "schedule-unscheduled-item";
      const component = entry.component.component ? ` · ${entry.component.component}` : "";
      item.textContent = `${entry.section.course_code}${component}`;
      item.addEventListener("click", () => openScheduleDetail(entry.key));
      list.appendChild(item);
    });
    unscheduledContainer.appendChild(list);
  }
}

function renderScheduleDetail(section, key) {
  const calendarView = document.querySelector("#schedule-calendar-view");
  const detailView = document.querySelector("#schedule-detail-view");
  const content = document.querySelector("#schedule-detail-content");
  if (!calendarView || !detailView || !content) return;
  calendarView.hidden = true;
  detailView.hidden = false;
  content.replaceChildren();

  const card = courseCard(section, { tooltipPrefix: "schedule-description" });
  card.classList.add("schedule-detail-course-card");
  content.appendChild(card);

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "schedule-remove-button";
  remove.textContent = "Remove from Schedule";
  remove.addEventListener("click", () => removeScheduleSection(key));
  content.appendChild(remove);
}

function openScheduleDetail(key) {
  const section = readSchedule()[key];
  if (!section || String(section.term || "").trim() !== state.selectedTerm) return;
  state.scheduleDetailKey = key;
  renderScheduleDrawer();
}

function showScheduleCalendar() {
  state.scheduleDetailKey = null;
  renderScheduleDrawer();
}

function renderScheduleDrawer() {
  const termLabel = document.querySelector("#schedule-term-label");
  const countLabel = document.querySelector("#schedule-entry-count");
  const calendarView = document.querySelector("#schedule-calendar-view");
  const detailView = document.querySelector("#schedule-detail-view");
  if (termLabel) termLabel.textContent = state.selectedTerm || "Current term";
  const sections = scheduledSections();
  if (countLabel) countLabel.textContent = `${sections.length} class${sections.length === 1 ? "" : "es"}`;

  const selected = state.scheduleDetailKey ? readSchedule()[state.scheduleDetailKey] : null;
  if (selected && String(selected.term || "").trim() === state.selectedTerm) {
    renderScheduleDetail(selected, state.scheduleDetailKey);
    return;
  }

  state.scheduleDetailKey = null;
  if (calendarView) calendarView.hidden = false;
  if (detailView) detailView.hidden = true;
  renderScheduleCalendar(sections);
}

function openScheduleDrawer() {
  const drawer = document.querySelector("#schedule-drawer");
  const nav = document.querySelector("#schedule-nav");
  if (!drawer) return;
  state.scheduleDrawerOpen = true;
  drawer.inert = false;
  drawer.classList.add("is-open");
  drawer.setAttribute("aria-hidden", "false");
  nav?.setAttribute("aria-expanded", "true");
  renderScheduleDrawer();
  void refreshScheduleSeats();
}

function closeScheduleDrawer({ focusToggle = false } = {}) {
  const drawer = document.querySelector("#schedule-drawer");
  const nav = document.querySelector("#schedule-nav");
  state.scheduleDrawerOpen = false;
  state.scheduleDetailKey = null;
  if (drawer) drawer.inert = true;
  drawer?.classList.remove("is-open");
  drawer?.setAttribute("aria-hidden", "true");
  nav?.setAttribute("aria-expanded", "false");
  if (focusToggle) nav?.focus();
}

function setupScheduleDrawer() {
  const nav = document.querySelector("#schedule-nav");
  const close = document.querySelector("#schedule-close");
  const back = document.querySelector("#schedule-detail-back");
  if (!nav) return;

  nav.addEventListener("click", () => {
    if (state.scheduleDrawerOpen) closeScheduleDrawer({ focusToggle: true });
    else openScheduleDrawer();
  });
  close?.addEventListener("click", () => closeScheduleDrawer({ focusToggle: true }));
  back?.addEventListener("click", showScheduleCalendar);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.scheduleDrawerOpen) {
      closeScheduleDrawer({ focusToggle: true });
    }
  });

  updateScheduleCount();
}
