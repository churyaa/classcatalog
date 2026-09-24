function renderAdminOperations(data) {
  const operations = data?.operations || {};
  const seats = data?.seat_refresh || {};
  const professors = data?.professors || {};

  const metrics = document.querySelector("#admin-operations-metrics");
  if (metrics) {
    const change = operations.section_count_change;
    const hasChange = change != null && Number.isFinite(Number(change));
    const changeLabel = hasChange
      ? `${Number(change) > 0 ? "+" : ""}${adminNumber(change)}`
      : "No prior snapshot";
    metrics.innerHTML = [
      adminMetric("Latest published scrape", operations.latest_published_at ? adminDate(operations.latest_published_at) : "Unavailable"),
      adminMetric("Incomplete subjects", adminNumber(operations.incomplete_subjects || 0), { tone: operations.incomplete_subjects ? "bad" : "good" }),
      adminMetric("Section-count change", changeLabel, { tone: Number(change) < 0 ? "warning" : "" }),
      adminMetric("New subjects", adminNumber((operations.subjects_added || []).length), { tone: (operations.subjects_added || []).length ? "good" : "" }),
      adminMetric("Removed subjects", adminNumber((operations.subjects_removed || []).length), { tone: (operations.subjects_removed || []).length ? "warning" : "good" }),
      adminMetric("Stale seat records", adminNumber(seats.stale_cached_sections || 0), { tone: seats.stale_cached_sections ? "warning" : "good" }),
      adminMetric("Professor matching failures", adminNumber(professors.unmatched || 0), { tone: professors.unmatched ? "warning" : "good" }),
    ].join("");
  }

  const note = document.querySelector("#admin-operations-note");
  if (note) {
    const parts = [];
    if (operations.incomplete_subjects_note) parts.push(operations.incomplete_subjects_note);
    if (operations.previous_snapshot) parts.push(`Comparison snapshot: ${operations.previous_snapshot}`);
    note.textContent = parts.join(" · ");
  }

  const termCoverage = document.querySelector("#admin-term-coverage");
  if (termCoverage) {
    termCoverage.replaceChildren();
    (operations.term_coverage || []).forEach((item) => {
      const row = document.createElement("div");
      row.className = "admin-ops-row";
      const label = document.createElement("strong");
      label.textContent = item.term || "Unknown term";
      const value = document.createElement("span");
      const hasDelta = item.change != null && Number.isFinite(Number(item.change));
      const delta = hasDelta
        ? ` · ${Number(item.change) >= 0 ? "+" : ""}${adminNumber(item.change)} vs previous snapshot`
        : "";
      value.textContent = `${adminNumber(item.sections)} sections${delta}`;
      row.append(label, value);
      termCoverage.appendChild(row);
    });
  }

  const subjectChanges = document.querySelector("#admin-subject-changes");
  if (subjectChanges) {
    subjectChanges.replaceChildren();
    const groups = [
      ["New subjects", operations.subjects_added || [], "is-good"],
      ["Removed subjects", operations.subjects_removed || [], "is-warning"],
    ];
    groups.forEach(([title, values, className]) => {
      const group = document.createElement("div");
      group.className = "admin-ops-change-group";
      const heading = document.createElement("strong");
      heading.textContent = title;
      group.appendChild(heading);
      if (!values.length) {
        const none = document.createElement("span");
        none.textContent = "None";
        group.appendChild(none);
      } else {
        const chips = document.createElement("div");
        chips.className = "admin-ops-chip-list";
        values.forEach((value) => {
          const chip = document.createElement("span");
          chip.className = `admin-ops-chip ${className}`;
          chip.textContent = value;
          chips.appendChild(chip);
        });
        group.appendChild(chips);
      }
      subjectChanges.appendChild(group);
    });
  }

  const failures = document.querySelector("#admin-professor-match-failures");
  if (failures) {
    failures.replaceChildren();
    const unmatched = Array.isArray(professors.unmatched_instructors) ? professors.unmatched_instructors : [];
    if (!unmatched.length) {
      const allMatched = document.createElement("p");
      allMatched.className = "admin-note";
      allMatched.textContent = "No unmatched active instructors.";
      failures.appendChild(allMatched);
    } else {
      unmatched.slice(0, 100).forEach((item) => {
        const row = document.createElement("div");
        row.className = "admin-professor-failure-row";
        const name = document.createElement("strong");
        name.textContent = item.name || "Unknown instructor";
        const courses = document.createElement("span");
        courses.textContent = (item.course_codes || []).join(", ") || "No active course code";
        row.append(name, courses);
        failures.appendChild(row);
      });
    }
  }
}
