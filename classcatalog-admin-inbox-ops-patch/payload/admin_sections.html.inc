
        <section class="admin-section" aria-labelledby="admin-announcements-title">
          <div class="admin-section-heading">
            <div>
              <p class="admin-kicker">Student communication</p>
              <h3 id="admin-announcements-title">Inbox announcements</h3>
            </div>
          </div>
          <form id="admin-announcement-form" class="admin-announcement-form">
            <div class="admin-announcement-fields">
              <label for="admin-announcement-title">Title
                <input id="admin-announcement-title" type="text" maxlength="120" placeholder="Registration update" autocomplete="off" data-no-search required>
              </label>
              <label for="admin-announcement-message">Message
                <textarea id="admin-announcement-message" maxlength="2000" placeholder="Write an announcement for every ClassCatalog visitor…" data-no-search required></textarea>
              </label>
              <button id="admin-announcement-send" class="admin-refresh" type="submit">Send Announcement</button>
            </div>
            <p id="admin-announcement-result" class="admin-seat-refresh-result" aria-live="polite"></p>
          </form>
          <div id="admin-announcement-history" class="admin-announcement-history"></div>
        </section>

        <section class="admin-section" aria-labelledby="admin-operations-title">
          <div class="admin-section-heading">
            <div>
              <p class="admin-kicker">Scrape and inventory intelligence</p>
              <h3 id="admin-operations-title">Operations overview</h3>
            </div>
          </div>
          <div id="admin-operations-metrics" class="admin-metric-grid"></div>
          <p id="admin-operations-note" class="admin-note"></p>
          <div class="admin-ops-detail-grid">
            <div class="admin-ops-panel">
              <h4>Term coverage</h4>
              <div id="admin-term-coverage" class="admin-ops-list"></div>
            </div>
            <div class="admin-ops-panel">
              <h4>Subject changes</h4>
              <div id="admin-subject-changes"></div>
            </div>
            <div class="admin-ops-panel">
              <h4>Professor matching failures</h4>
              <div id="admin-professor-match-failures" class="admin-professor-failure-list"></div>
            </div>
          </div>
        </section>
