const inboxReadStorageKey = "classcatalog_inbox_read_v1";

function readInboxReadIds() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(inboxReadStorageKey) || "[]");
    return new Set(Array.isArray(parsed) ? parsed.map(String) : []);
  } catch {
    return new Set();
  }
}

function writeInboxReadIds(ids) {
  try {
    window.localStorage.setItem(inboxReadStorageKey, JSON.stringify([...ids]));
  } catch {
    // Inbox read state is only a browser convenience.
  }
}

function announcementAge(value) {
  const timestamp = Date.parse(value || "");
  if (!Number.isFinite(timestamp)) return "";
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
  const months = Math.floor(days / 30);
  return `${months} month${months === 1 ? "" : "s"} ago`;
}

function unreadAnnouncementCount() {
  const read = readInboxReadIds();
  return state.announcements.filter((item) => item?.id && !read.has(String(item.id))).length;
}

function updateInboxCount() {
  const count = document.querySelector("#inbox-count");
  if (!count) return;
  count.textContent = String(unreadAnnouncementCount());
}

function renderInboxDrawer() {
  const list = document.querySelector("#inbox-message-list");
  const empty = document.querySelector("#inbox-empty");
  const subtitle = document.querySelector("#inbox-subtitle");
  if (!list || !empty) return;

  list.replaceChildren();
  const messages = Array.isArray(state.announcements) ? state.announcements : [];
  empty.hidden = messages.length > 0;
  if (subtitle) subtitle.textContent = `${messages.length} message${messages.length === 1 ? "" : "s"}`;

  messages.forEach((item) => {
    const article = document.createElement("article");
    article.className = "inbox-message";

    const meta = document.createElement("p");
    meta.className = "inbox-message-age";
    meta.textContent = announcementAge(item.created_at);

    const title = document.createElement("h3");
    title.textContent = item.title || "Announcement";

    const body = document.createElement("p");
    body.className = "inbox-message-body";
    body.textContent = item.message || "";

    article.append(meta, title, body);
    list.appendChild(article);
  });
}

function markCurrentAnnouncementsRead() {
  const read = readInboxReadIds();
  state.announcements.forEach((item) => {
    if (item?.id) read.add(String(item.id));
  });
  writeInboxReadIds(read);
  updateInboxCount();
}

async function loadAnnouncements() {
  try {
    const payload = await fetchJson("/api/announcements", { cache: "no-store" });
    state.announcements = Array.isArray(payload.messages) ? payload.messages : [];
    updateInboxCount();
    renderAdminAnnouncementHistory();
    if (state.inboxDrawerOpen) renderInboxDrawer();
  } catch {
    // Announcements should never interfere with class browsing.
  }
}

function openInboxDrawer() {
  const drawer = document.querySelector("#inbox-drawer");
  const nav = document.querySelector("#inbox-nav");
  if (!drawer) return;
  if (state.scheduleDrawerOpen) closeScheduleDrawer();
  state.inboxDrawerOpen = true;
  drawer.inert = false;
  drawer.classList.add("is-open");
  drawer.setAttribute("aria-hidden", "false");
  nav?.setAttribute("aria-expanded", "true");
  renderInboxDrawer();
  markCurrentAnnouncementsRead();
}

function closeInboxDrawer({ focusToggle = false } = {}) {
  const drawer = document.querySelector("#inbox-drawer");
  const nav = document.querySelector("#inbox-nav");
  state.inboxDrawerOpen = false;
  if (drawer) drawer.inert = true;
  drawer?.classList.remove("is-open");
  drawer?.setAttribute("aria-hidden", "true");
  nav?.setAttribute("aria-expanded", "false");
  if (focusToggle) nav?.focus({ preventScroll: true });
}

function setupInboxDrawer() {
  const nav = document.querySelector("#inbox-nav");
  const close = document.querySelector("#inbox-close");
  if (!nav) return;
  nav.addEventListener("click", () => {
    if (state.inboxDrawerOpen) closeInboxDrawer({ focusToggle: true });
    else openInboxDrawer();
  });
  close?.addEventListener("click", () => closeInboxDrawer({ focusToggle: true }));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.inboxDrawerOpen) {
      closeInboxDrawer({ focusToggle: true });
    }
  });
  updateInboxCount();
}

function renderAdminAnnouncementHistory() {
  const list = document.querySelector("#admin-announcement-history");
  if (!list) return;
  list.replaceChildren();
  const messages = Array.isArray(state.announcements) ? state.announcements : [];
  if (!messages.length) {
    const empty = document.createElement("p");
    empty.className = "admin-note";
    empty.textContent = "No announcements have been sent yet.";
    list.appendChild(empty);
    return;
  }
  messages.forEach((item) => {
    const row = document.createElement("div");
    row.className = "admin-announcement-history-item";
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.title || "Announcement";
    const meta = document.createElement("span");
    meta.textContent = adminDate(item.created_at);
    const body = document.createElement("p");
    body.textContent = item.message || "";
    copy.append(title, meta, body);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "admin-refresh";
    remove.textContent = "Delete";
    remove.addEventListener("click", () => deleteAdminAnnouncement(item.id, remove));
    row.append(copy, remove);
    list.appendChild(row);
  });
}

async function sendAdminAnnouncement(event) {
  event.preventDefault();
  const title = document.querySelector("#admin-announcement-title");
  const message = document.querySelector("#admin-announcement-message");
  const button = document.querySelector("#admin-announcement-send");
  const result = document.querySelector("#admin-announcement-result");
  if (!title || !message || !button) return;
  button.disabled = true;
  if (result) {
    result.textContent = "Sending announcement…";
    result.className = "admin-seat-refresh-result";
  }
  try {
    await fetchJson("/api/admin/announcements", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title.value.trim(), message: message.value.trim() }),
    }, { fallbackMessage: "Announcement could not be sent.", allowDetail: true });
    title.value = "";
    message.value = "";
    await loadAnnouncements();
    if (result) {
      result.textContent = "Announcement sent to the Inbox.";
      result.className = "admin-seat-refresh-result is-success";
    }
  } catch (error) {
    if (result) {
      result.textContent = safeErrorMessage(error, "Announcement could not be sent.");
      result.className = "admin-seat-refresh-result is-error";
    }
  } finally {
    button.disabled = false;
  }
}

async function deleteAdminAnnouncement(messageId, button) {
  if (!messageId || !button) return;
  button.disabled = true;
  try {
    await fetchJson(`/api/admin/announcements/${encodeURIComponent(messageId)}`, {
      method: "DELETE",
      cache: "no-store",
    }, { fallbackMessage: "Announcement could not be deleted.", allowDetail: true });
    await loadAnnouncements();
  } catch (error) {
    button.disabled = false;
    const result = document.querySelector("#admin-announcement-result");
    if (result) {
      result.textContent = safeErrorMessage(error, "Announcement could not be deleted.");
      result.className = "admin-seat-refresh-result is-error";
    }
  }
}
// Inbox drawer outside-click behavior v1
document.addEventListener("click", (event) => {
  if (!state.inboxDrawerOpen) return;

  const target = event.target;
  if (!(target instanceof Element)) return;

  if (target.closest("#inbox-drawer") || target.closest("#inbox-nav")) return;
  closeInboxDrawer();
});
