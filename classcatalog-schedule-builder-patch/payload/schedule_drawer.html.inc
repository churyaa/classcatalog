  <div id="service-alerts" class="service-alerts" role="status" aria-live="polite" hidden></div>

  <aside id="schedule-drawer" class="schedule-drawer" aria-label="Visual schedule builder" aria-hidden="true" inert>
    <div class="schedule-drawer-header">
      <div class="schedule-drawer-title">
        <svg class="schedule-drawer-title-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M10 21H6.2C5.0799 21 4.51984 21 4.09202 20.782C3.71569 20.5903 3.40973 20.2843 3.21799 19.908C3 19.4802 3 18.9201 3 17.8V8.2C3 7.0799 3 6.51984 3.21799 6.09202C3.40973 5.71569 3.71569 5.40973 4.09202 5.21799C4.51984 5 5.0799 5 6.2 5H17.8C18.9201 5 19.4802 5 19.908 5.21799C20.2843 5.40973 20.5903 5.71569 20.782 6.09202C21 6.51984 21 7.0799 21 8.2V10M7 3V5M17 3V5M3 9H21M13.5 13.0001L7 13M10 17.0001L7 17M14 21L16.025 20.595C16.2015 20.5597 16.2898 20.542 16.3721 20.5097C16.4452 20.4811 16.5147 20.4439 16.579 20.399C16.6516 20.3484 16.7152 20.2848 16.8426 20.1574L21 16C21.5523 15.4477 21.5523 14.5523 21 14C20.4477 13.4477 19.5523 13.4477 19 14L14.8426 18.1574C14.7152 18.2848 14.6516 18.3484 14.601 18.421C14.5561 18.4853 14.5189 18.5548 14.4903 18.6279C14.458 18.7102 14.4403 18.7985 14.405 18.975L14 21Z"></path>
        </svg>
        <div>
          <h2>My Schedule</h2>
          <p><span id="schedule-term-label">Current term</span> · <span id="schedule-entry-count">0 classes</span></p>
        </div>
      </div>
      <button id="schedule-close" class="schedule-drawer-close" type="button" aria-label="Close schedule">×</button>
    </div>
    <div class="schedule-drawer-body">
      <section id="schedule-calendar-view">
        <div class="schedule-calendar-toolbar">
          <strong>Weekly calendar</strong>
          <span>Click a class for full details</span>
        </div>
        <p id="schedule-empty" class="schedule-empty">Add a class from the search results to start building this semester's schedule.</p>
        <div class="schedule-calendar-scroll">
          <div id="schedule-calendar" hidden></div>
        </div>
        <section id="schedule-unscheduled" class="schedule-unscheduled" aria-label="Asynchronous or TBA classes" hidden></section>
      </section>
      <section id="schedule-detail-view" class="schedule-detail-view" hidden>
        <div class="schedule-detail-header">
          <button id="schedule-detail-back" class="schedule-detail-back" type="button">← Calendar</button>
        </div>
        <div id="schedule-detail-content" class="schedule-detail-content"></div>
      </section>
    </div>
  </aside>

  <main id="browse-page">
