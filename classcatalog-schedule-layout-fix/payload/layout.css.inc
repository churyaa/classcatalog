
/* Visual schedule builder: preserve professor panel width ---------------- */
@media (min-width: 1051px) {
  #course-list .course-card:not(.course-card-grouped),
  #favorites-list .course-card:not(.course-card-grouped) {
    grid-template-columns: minmax(0, 1fr) minmax(360px, .95fr);
  }
}

.course-side-panel {
  grid-template-columns: minmax(7.6rem, 8.4rem) minmax(13.5rem, 1fr);
}

/* Grouped options have a much narrower professor cell. Stack the action
   above the professor content there so RateMyProfessors data keeps the same
   width it had before the schedule builder was added. */
.component-professor-layout.with-schedule {
  grid-template-columns: 1fr;
}
.component-professor-layout.with-schedule > .schedule-add-button {
  width: 100%;
  margin-bottom: .42rem;
}

@media (max-width: 1050px) {
  .course-side-panel {
    grid-template-columns: minmax(7.6rem, 9rem) minmax(13.5rem, 1fr);
  }
}

@media (max-width: 720px) {
  .course-side-panel { grid-template-columns: 1fr; }
  .course-side-panel > .schedule-add-button { width: min(100%, 13rem); }
}
