
/* Grouped schedule action: use the empty area above the component table. */
.component-group-toolbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: .8rem;
  margin-bottom: .38rem;
}
.component-group-toolbar .component-group-title {
  margin: 0;
}
.component-group-toolbar > .schedule-add-button {
  width: min(11.5rem, 100%);
  flex: 0 0 auto;
}

@media (max-width: 720px) {
  .component-group-toolbar {
    align-items: stretch;
    flex-direction: column;
  }
  .component-group-toolbar > .schedule-add-button {
    width: min(100%, 13rem);
    align-self: flex-end;
  }
}
