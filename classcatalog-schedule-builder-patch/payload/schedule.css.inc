/* Visual schedule builder ------------------------------------------------- */
.schedule-nav-button { cursor: pointer; font: inherit; }
.schedule-drawer {
  position: fixed;
  inset: 0 0 0 auto;
  z-index: 80;
  display: flex;
  width: min(820px, 72vw);
  height: 100dvh;
  flex-direction: column;
  color: var(--theme-text);
  border-left: 1px solid var(--line);
  background: color-mix(in srgb, var(--theme-background) 94%, var(--theme-ui));
  box-shadow: -20px 0 55px color-mix(in srgb, var(--theme-text) 18%, transparent);
  transform: translateX(calc(100% + 1rem));
  transition: transform .2s ease;
}
.schedule-drawer.is-open { transform: translateX(0); }
.schedule-drawer-header {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 1rem 1.05rem;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
.schedule-drawer-title { display: flex; min-width: 0; align-items: center; gap: .7rem; }
.schedule-drawer-title-icon {
  width: 1.55rem;
  height: 1.55rem;
  flex: 0 0 1.55rem;
  fill: none;
  stroke: var(--theme-accent);
  stroke-width: 1.9;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.schedule-drawer-title h2 { margin: 0; font-size: 1.16rem; line-height: 1.15; }
.schedule-drawer-title p { margin: .16rem 0 0; color: var(--muted); font-size: .7rem; font-weight: 750; }
.schedule-drawer-close,
.schedule-detail-back {
  min-height: 2.2rem;
  padding: .45rem .68rem;
  border: 1px solid var(--line);
  border-radius: .62rem;
  color: var(--theme-text);
  background: var(--surface-raised);
  cursor: pointer;
  font: inherit;
  font-size: .74rem;
  font-weight: 850;
}
.schedule-drawer-close { width: 2.2rem; padding: 0; font-size: 1.25rem; line-height: 1; }
.schedule-drawer-close:hover,
.schedule-detail-back:hover { border-color: var(--theme-accent); color: var(--theme-accent); }
.schedule-drawer-body { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 1rem; }
.schedule-calendar-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: .8rem;
  margin-bottom: .75rem;
  padding: .72rem .8rem;
  border: 1px solid var(--line);
  border-radius: .75rem;
  background: var(--surface);
}
.schedule-calendar-toolbar strong { font-size: .86rem; }
.schedule-calendar-toolbar span { color: var(--muted); font-size: .7rem; font-weight: 800; }
.schedule-empty {
  margin: 1rem 0 0;
  padding: 2rem 1rem;
  border: 1px dashed var(--line);
  border-radius: .85rem;
  color: var(--muted);
  background: var(--surface-soft);
  text-align: center;
  font-size: .82rem;
  line-height: 1.5;
}
.schedule-calendar-scroll { overflow-x: auto; padding-bottom: .4rem; }
.schedule-calendar-week {
  display: grid;
  min-width: 660px;
  grid-template-columns: 3.7rem repeat(var(--schedule-day-count), minmax(112px, 1fr));
  grid-template-rows: 2.25rem var(--schedule-calendar-height);
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: .8rem;
  background: var(--surface-soft);
}
.schedule-calendar-corner,
.schedule-day-header {
  position: sticky;
  top: 0;
  z-index: 3;
  display: grid;
  place-items: center;
  border-bottom: 1px solid var(--line);
  background: var(--surface-raised);
}
.schedule-calendar-corner { border-right: 1px solid var(--line); }
.schedule-day-header {
  color: var(--muted);
  font-size: .64rem;
  font-weight: 900;
  letter-spacing: .055em;
  text-transform: uppercase;
}
.schedule-day-header + .schedule-day-header { border-left: 1px solid var(--line); }
.schedule-time-axis {
  position: relative;
  border-right: 1px solid var(--line);
  background: var(--surface);
}
.schedule-time-label {
  position: absolute;
  right: .42rem;
  color: var(--muted);
  font-size: .56rem;
  font-weight: 800;
  transform: translateY(-.5em);
  white-space: nowrap;
}
.schedule-day-column {
  position: relative;
  min-width: 0;
  border-right: 1px solid var(--line);
  background:
    repeating-linear-gradient(
      to bottom,
      transparent 0,
      transparent 59px,
      color-mix(in srgb, var(--line) 68%, transparent) 59px,
      color-mix(in srgb, var(--line) 68%, transparent) 60px
    ),
    var(--surface-soft);
}
.schedule-day-column:last-child { border-right: 0; }
.schedule-calendar-block {
  position: absolute;
  left: 3px;
  right: 3px;
  z-index: 2;
  display: flex;
  min-height: 22px;
  flex-direction: column;
  align-items: flex-start;
  gap: .05rem;
  overflow: hidden;
  padding: .24rem .3rem;
  border: 1px solid var(--theme-accent);
  border-radius: .42rem;
  color: var(--theme-text);
  background: color-mix(in srgb, var(--theme-accent) var(--schedule-tint), var(--surface-raised));
  cursor: pointer;
  font: inherit;
  text-align: left;
}
.schedule-calendar-block:hover,
.schedule-calendar-block:focus-visible {
  z-index: 4;
  filter: brightness(1.04);
  outline: 2px solid var(--theme-accent);
  outline-offset: 1px;
}
.schedule-calendar-block strong { font-size: .67rem; line-height: 1.1; }
.schedule-calendar-block span,
.schedule-calendar-block small { max-width: 100%; overflow: hidden; font-size: .55rem; line-height: 1.1; text-overflow: ellipsis; white-space: nowrap; }
.schedule-unscheduled { margin-top: .8rem; padding: .78rem; border: 1px solid var(--line); border-radius: .75rem; background: var(--surface); }
.schedule-unscheduled h3 { margin: 0 0 .55rem; font-size: .72rem; }
.schedule-unscheduled-list { display: flex; flex-wrap: wrap; gap: .4rem; }
.schedule-unscheduled-item {
  padding: .42rem .55rem;
  border: 1px solid var(--line);
  border-radius: .55rem;
  color: var(--theme-text);
  background: var(--surface-raised);
  cursor: pointer;
  font: inherit;
  font-size: .7rem;
  font-weight: 800;
}
.schedule-unscheduled-item:hover { border-color: var(--theme-accent); color: var(--theme-accent); }
.schedule-add-button {
  min-height: 2.35rem;
  padding: .46rem .62rem;
  border: 1px solid var(--theme-accent);
  border-radius: .62rem;
  color: var(--theme-accent);
  background: color-mix(in srgb, var(--theme-accent) 8%, var(--surface-raised));
  cursor: pointer;
  font: inherit;
  font-size: .7rem;
  font-weight: 900;
  line-height: 1.2;
  text-align: center;
}
.schedule-add-button:hover:not(:disabled) { background: color-mix(in srgb, var(--theme-accent) 15%, var(--surface-raised)); }
.schedule-add-button:disabled { cursor: not-allowed; opacity: .72; }
.schedule-add-button.is-scheduled { color: var(--muted); border-color: var(--line); background: var(--surface-soft); }
.schedule-add-button.has-overlap { color: var(--muted); border-color: var(--line); background: var(--surface-soft); }
.course-side-panel {
  display: grid;
  grid-template-columns: minmax(8.7rem, .62fr) minmax(0, 1fr);
  gap: .65rem;
  align-items: start;
}
.course-side-panel > .schedule-add-button { width: 100%; }
.component-professor-layout.with-schedule {
  display: grid;
  grid-template-columns: minmax(7.8rem, .7fr) minmax(0, 1fr);
  gap: .5rem;
  align-items: start;
}
.component-professor-copy { min-width: 0; }
.schedule-detail-view { min-width: 0; }
.schedule-detail-header { margin-bottom: .75rem; }
.schedule-detail-content { display: grid; gap: .75rem; }
.schedule-detail-course-card { margin: 0; }
.schedule-detail-content .course-card { grid-template-columns: 1fr; }
.schedule-remove-button {
  min-height: 2.55rem;
  padding: .58rem .8rem;
  border: 1px solid color-mix(in srgb, #c52b3d 70%, var(--line));
  border-radius: .65rem;
  color: color-mix(in srgb, #c52b3d 76%, var(--theme-text));
  background: color-mix(in srgb, #c52b3d 8%, var(--surface));
  cursor: pointer;
  font: inherit;
  font-size: .75rem;
  font-weight: 900;
}
.schedule-remove-button:hover { background: color-mix(in srgb, #c52b3d 14%, var(--surface)); }
@media (max-width: 1050px) {
  .course-side-panel { grid-template-columns: minmax(8.7rem, 180px) minmax(0, 1fr); }
}
@media (max-width: 900px) {
  .schedule-drawer { width: 100vw; }
}
@media (max-width: 720px) {
  .component-professor-layout.with-schedule { grid-template-columns: 1fr; }
  .component-professor-layout.with-schedule > .schedule-add-button { width: min(100%, 13rem); }
  .course-side-panel { grid-template-columns: 1fr; }
  .course-side-panel > .schedule-add-button { width: min(100%, 13rem); }
  .schedule-drawer-body { padding: .75rem; }
  .schedule-calendar-week { min-width: 620px; }
}
