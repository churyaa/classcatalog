"""Offline regression tests for verified live PeopleSoft partition navigation."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from classcatalog.scraping.session import (
    PeopleSoftTransientError,
    RawSubjectPages,
    SdsuHttpConfig,
    SdsuPeopleSoftSession,
    _LivePartitionState,
)

URL = "https://example.test/results?SEARCH_TEXT=BIOL&ES_STRM=2267"
GRAD = (("Course Career", "Graduate"),)
UNDERGRAD = (("Course Career", "Undergraduate"),)
UG_UNITS = UNDERGRAD + (("Number of Units", "1 - 3 Units"),)


def page(*, career: str | None = None, units: str | None = None, capped: bool = False) -> str:
    def checked(value: str | None, expected: str) -> str:
        return " checked" if value == expected else ""
    cap = "Search results have exceeded a limit set by your institution." if capped else ""
    return f"""
    <html><body><form id='win0' method='post' action='/results'>
      <input type='hidden' name='ICStateNum' value='12'>
      <input type='hidden' name='ICSID' value='state'>
      <input type='hidden' name='PTS_SELECT$chk$0' value='N'>
      <input type='checkbox' id='PTS_SELECT$0' name='PTS_SELECT$0' value='Y'>
      <label for='PTS_SELECT$0'>Open Classes Only</label>
      <input type='hidden' name='PTS_SELECT$chk$6' value='Y'>
      <input type='checkbox' id='PTS_SELECT$6' name='PTS_SELECT$6' value='Y' checked>
      <label for='PTS_SELECT$6'>BIOL/Biology</label>
      <fieldset><legend>Course Career</legend>
        <input type='checkbox' name='PTS_SELECT$3' id='PTS_SELECT$3' value='Y'{checked(career, 'Graduate')}>
        <label for='PTS_SELECT$3'>Graduate</label>
        <input type='checkbox' name='PTS_SELECT$4' id='PTS_SELECT$4' value='Y'{checked(career, 'Undergraduate')}>
        <label for='PTS_SELECT$4'>Undergraduate</label>
      </fieldset>
      <fieldset><legend>Number of Units</legend>
        <input type='checkbox' name='PTS_SELECT$10' id='PTS_SELECT$10' value='Y'{checked(units, '1 - 3 Units')}>
        <label for='PTS_SELECT$10'>1 - 3 Units</label>
        <input type='checkbox' name='PTS_SELECT$11' id='PTS_SELECT$11' value='Y'{checked(units, 'No Value')}>
        <label for='PTS_SELECT$11'>No Value</label>
      </fieldset><div>{cap}</div>
    </form></body></html>
    """


def pages() -> RawSubjectPages:
    base = page(capped=True)
    return RawSubjectPages(
        subject="BIOL", term="Fall 2026", term_code="2267",
        search_url=URL, initial_html=base, filtered_html=base,
        exact_facet_applied=True,
    )


def client() -> SdsuPeopleSoftSession:
    session = SdsuPeopleSoftSession(SdsuHttpConfig(
        delay_seconds=0, jitter_seconds=0, max_retries=0,
        post_recovery_cooldown_seconds=0,
        partition_live_navigation=True,
    ))
    session._live_partition_state = _LivePartitionState(
        "Fall 2026", "2267", "BIOL", (), page(capped=True), URL,
    )
    return session


def test_live_path_uses_current_post_response_for_next_facet(monkeypatch) -> None:
    session = client()
    requests = []
    responses = {
        "PTS_SELECT$3": page(career="Graduate"),
        "PTS_SELECT$4": page(career="Undergraduate"),
        "PTS_SELECT$10": page(career="Undergraduate", units="1 - 3 Units"),
    }

    def request(method, url, **kwargs):
        assert method == "POST"  # no GET/bootstrap for adjacent branches
        post_fields = dict(kwargs["data"])
        action = post_fields["ICAction"]
        requests.append(action)
        return SimpleNamespace(text=responses[action], url=URL)

    monkeypatch.setattr(session, "_request", request)
    try:
        assert session._try_live_partition_path(pages=pages(), filters=GRAD) is not None
        assert session._try_live_partition_path(pages=pages(), filters=UNDERGRAD) is not None
        assert session._try_live_partition_path(pages=pages(), filters=UG_UNITS) is not None
        assert requests == ["PTS_SELECT$3", "PTS_SELECT$4", "PTS_SELECT$10"]
        assert session._live_partition_state.filters == UG_UNITS
        # Re-reading the *same current* live path never triggers another POST.
        session._try_live_partition_path(pages=pages(), filters=UG_UNITS)
        assert len(requests) == 3
    finally:
        session.close()


def test_unrelated_or_backtracking_path_declines_without_http(monkeypatch) -> None:
    session = client()
    session._live_partition_state = _LivePartitionState(
        "Fall 2026", "2267", "BIOL", UG_UNITS,
        page(career="Undergraduate", units="1 - 3 Units"), URL,
    )
    monkeypatch.setattr(session, "_request", lambda *a, **k: pytest.fail("unexpected HTTP request"))
    try:
        assert session._try_live_partition_path(pages=pages(), filters=GRAD) is None
        assert session._try_live_partition_path(
            pages=RawSubjectPages(
                subject="ART", term="Spring 2027", term_code="2273",
                search_url=URL, initial_html="", filtered_html="",
                exact_facet_applied=True,
            ),
            filters=UNDERGRAD,
        ) is None
        assert session._live_partition_state.filters == UG_UNITS
    finally:
        session.close()


def test_live_post_timeout_resets_session_and_rebuilds_from_fresh_path(monkeypatch) -> None:
    session = client()
    counters = {"posts": 0, "resets": 0, "rebuilds": 0}

    def fails(method, url, **kwargs):
        assert method == "POST"
        counters["posts"] += 1
        raise PeopleSoftTransientError(method="POST", url=URL)

    def reset():
        counters["resets"] += 1
        session._live_partition_state = None

    def rebuild(*, term, term_code, subject, filters):
        assert (term, term_code, subject, filters) == ("Fall 2026", "2267", "BIOL", GRAD)
        counters["rebuilds"] += 1
        return page(career="Graduate"), URL

    monkeypatch.setattr(session, "_request", fails)
    monkeypatch.setattr(session, "reset_public_session", reset)
    monkeypatch.setattr(session, "_fetch_subject_partition_path", rebuild)
    try:
        html, url = session._fetch_live_or_rebuild_partition(pages=pages(), filters=GRAD)
        assert "Graduate" in html and url == URL
        assert counters == {"posts": 1, "resets": 1, "rebuilds": 1}
        assert session._live_partition_state.filters == GRAD
    finally:
        session.close()


def test_session_reset_invalidates_live_state() -> None:
    session = client()
    try:
        assert session._live_partition_state is not None
        session.reset_public_session()
        assert session._live_partition_state is None
    finally:
        session.close()


def test_live_navigation_is_opt_in_and_default_off() -> None:
    session = SdsuPeopleSoftSession(SdsuHttpConfig(delay_seconds=0))
    try:
        session._live_partition_state = _LivePartitionState(
            "Fall 2026", "2267", "BIOL", (), page(capped=True), URL,
        )
        assert session._try_live_partition_path(pages=pages(), filters=GRAD) is None
    finally:
        session.close()
