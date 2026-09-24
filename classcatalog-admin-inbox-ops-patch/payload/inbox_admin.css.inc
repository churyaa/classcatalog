
/* Inbox and admin inventory intelligence --------------------------------- */
.inbox-nav-button { cursor: pointer; font: inherit; }
.inbox-bell-icon { fill: currentColor; stroke: none; }
.inbox-drawer {
  position: fixed;
  inset: 0 0 0 auto;
  z-index: 82;
  display: flex;
  width: min(430px, 100vw);
  height: 100dvh;
  flex-direction: column;
  color: var(--theme-text);
  border-left: 1px solid var(--line);
  background: color-mix(in srgb, var(--theme-background) 94%, var(--theme-ui));
  box-shadow: -20px 0 55px color-mix(in srgb, var(--theme-text) 18%, transparent);
  transform: translateX(calc(100% + 1rem));
  transition: transform .2s ease;
}
.inbox-drawer.is-open { transform: translateX(0); }
.inbox-drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 1rem 1.05rem;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
.inbox-drawer-title { display: flex; min-width: 0; align-items: center; gap: .7rem; }
.inbox-drawer-title .inbox-bell-icon { width: 1.45rem; height: 1.45rem; flex: 0 0 1.45rem; color: var(--theme-accent); }
.inbox-drawer-title h2 { margin: 0; font-size: 1.16rem; }
.inbox-drawer-title p { margin: .12rem 0 0; color: var(--muted); font-size: .7rem; font-weight: 750; }
.inbox-drawer-close {
  width: 2.2rem;
  height: 2.2rem;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: .62rem;
  color: var(--theme-text);
  background: var(--surface-raised);
  cursor: pointer;
  font-size: 1.25rem;
  font-weight: 800;
}
.inbox-drawer-close:hover { border-color: var(--theme-accent); color: var(--theme-accent); }
.inbox-drawer-body { min-height: 0; flex: 1 1 auto; overflow-y: auto; padding: .85rem 1rem 1.2rem; }
.inbox-empty {
  margin: .5rem 0;
  padding: 2rem 1rem;
  border: 1px dashed var(--line);
  border-radius: .8rem;
  color: var(--muted);
  background: var(--surface-soft);
  text-align: center;
  font-size: .8rem;
}
.inbox-message-list { display: grid; }
.inbox-message {
  position: relative;
  padding: .85rem .7rem .95rem .95rem;
  border-left: 3px solid color-mix(in srgb, var(--theme-ui) 26%, transparent);
}
.inbox-message + .inbox-message { border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent); }
.inbox-message-age { margin: 0 0 .28rem; color: var(--muted); font-size: .65rem; }
.inbox-message h3 { margin: 0 0 .3rem; color: var(--theme-ui); font-size: .78rem; font-weight: 800; }
.inbox-message-body { margin: 0; color: var(--theme-text); font-size: .78rem; line-height: 1.42; white-space: pre-wrap; overflow-wrap: anywhere; }

.admin-announcement-form { display: grid; gap: .7rem; }
.admin-announcement-fields { display: grid; grid-template-columns: minmax(180px, .45fr) minmax(0, 1fr) auto; gap: .7rem; align-items: end; }
.admin-announcement-fields label { display: grid; gap: .35rem; }
.admin-announcement-fields textarea {
  width: 100%;
  min-height: 5.5rem;
  resize: vertical;
  padding: .68rem .72rem;
  border: 1px solid var(--line);
  border-radius: .65rem;
  color: var(--theme-text);
  background: var(--surface-raised);
  outline: none;
  font: inherit;
  font-size: .78rem;
}
.admin-announcement-fields textarea:focus { border-color: var(--theme-accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--theme-accent) 15%, transparent); }
.admin-announcement-history { display: grid; gap: .55rem; margin-top: .85rem; }
.admin-announcement-history-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: .8rem;
  padding: .72rem .8rem;
  border: 1px solid var(--line);
  border-radius: .7rem;
  background: var(--surface-soft);
}
.admin-announcement-history-item > div { min-width: 0; }
.admin-announcement-history-item strong { display: block; font-size: .77rem; }
.admin-announcement-history-item span { display: block; margin-top: .12rem; color: var(--muted); font-size: .64rem; }
.admin-announcement-history-item p { margin: .35rem 0 0; font-size: .73rem; line-height: 1.4; white-space: pre-wrap; overflow-wrap: anywhere; }

.admin-ops-detail-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .8rem; margin-top: .85rem; }
.admin-ops-panel { min-width: 0; padding: .8rem; border: 1px solid var(--line); border-radius: .75rem; background: var(--surface-soft); }
.admin-ops-panel h4 { margin: 0 0 .55rem; font-size: .74rem; }
.admin-ops-list { display: grid; gap: .42rem; }
.admin-ops-row { display: flex; align-items: baseline; justify-content: space-between; gap: .8rem; padding: .36rem 0; border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent); }
.admin-ops-row:last-child { border-bottom: 0; }
.admin-ops-row strong { font-size: .7rem; }
.admin-ops-row span { color: var(--muted); font-size: .67rem; text-align: right; }
.admin-ops-change-group + .admin-ops-change-group { margin-top: .65rem; }
.admin-ops-change-group > strong { display: block; margin-bottom: .36rem; font-size: .7rem; }
.admin-ops-change-group > span { color: var(--muted); font-size: .68rem; }
.admin-ops-chip-list { display: flex; flex-wrap: wrap; gap: .3rem; }
.admin-ops-chip { padding: .24rem .4rem; border: 1px solid var(--line); border-radius: 999px; font-size: .62rem; font-weight: 800; }
.admin-ops-chip.is-good { color: #075c33; border-color: #bfe6cf; background: #e4f5eb; }
.admin-ops-chip.is-warning { color: #7a4a00; border-color: #f1d88f; background: #fff2cf; }
.admin-professor-failure-list { display: grid; gap: .35rem; max-height: 18rem; overflow-y: auto; }
.admin-professor-failure-row { display: grid; gap: .12rem; padding: .4rem 0; border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent); }
.admin-professor-failure-row:last-child { border-bottom: 0; }
.admin-professor-failure-row strong { font-size: .7rem; }
.admin-professor-failure-row span { color: var(--muted); font-size: .65rem; }

@media (max-width: 980px) {
  .admin-ops-detail-grid { grid-template-columns: 1fr; }
  .admin-announcement-fields { grid-template-columns: 1fr; }
  .admin-announcement-fields .admin-refresh { justify-self: start; }
}
/* Inbox bell desktop alignment v1 */
@media (min-width: 701px) {
  .header-actions {
    flex: 1 1 auto;
  }

  #inbox-nav {
    order: 999;
    margin-left: auto;
  }
}
/* Compact Inbox drawer v1 */
.inbox-drawer {
  width: min(340px, calc(100vw - 24px));
  max-width: 340px;
  min-width: 0;
}
