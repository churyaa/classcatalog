
/* Schedule/course layout polish ------------------------------------------- */
/* Keep the professor panel at the full card height while the schedule action
   remains a compact button at the top of its column. */
.course-side-panel {
  align-self: stretch;
  align-items: stretch;
}
.course-side-panel > .schedule-add-button {
  align-self: start;
}
.course-side-panel > .professor-panel {
  height: 100%;
  min-height: 100%;
}

/* Grouped class options use the same horizontal tracks as a normal course
   card. This puts Add to Schedule at the same top position and x-coordinate
   as the button on single-section cards. */
.course-card-heading-row.is-grouped {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(360px, .95fr);
  gap: 1rem;
  align-items: start;
}
.grouped-schedule-side {
  display: grid;
  grid-template-columns: minmax(7.6rem, 8.4rem) minmax(13.5rem, 1fr);
  gap: .65rem;
  align-items: start;
}
.grouped-schedule-side > .schedule-add-button {
  grid-column: 1;
  width: 100%;
}

/* The results column keeps .25rem of right breathing room. Match the search
   toolbar's right edge to the course-card edge instead of extending past it. */
@media (min-width: 761px) {
  .toolbar {
    width: calc(100% - .25rem);
  }
}

@media (max-width: 1050px) {
  .course-card-heading-row.is-grouped {
    grid-template-columns: 1fr;
  }
  .grouped-schedule-side {
    grid-template-columns: minmax(7.6rem, 9rem) minmax(13.5rem, 1fr);
    margin-top: .65rem;
  }
}

@media (max-width: 720px) {
  .grouped-schedule-side {
    grid-template-columns: 1fr;
  }
  .grouped-schedule-side > .schedule-add-button {
    width: min(100%, 13rem);
  }
}
