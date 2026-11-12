"""Offline regressions for durable PeopleSoft 75-row result-cap partitioning."""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import pytest

from classcatalog.scraping.partition_cache import PartitionBranchCache
from classcatalog.scraping.session import (
    PeopleSoftTransientError,
    RawSubjectPages,
    SdsuHttpConfig,
    SdsuPeopleSoftSession,
)

URL = "https://example.test/results?SEARCH_TEXT=BIOL&ES_STRM=2267"
GRAD = (("Course Career", "Graduate"),)
UNDERGRAD = (("Course Career", "Undergraduate"),)


def _row(number: str, career: str) -> str:
    return f"""
      <li class='ps_grid-row psc_rowact'>
        <p hidden>BIOL {number}</p>
        <h3 class='course-title'>Biology {number}</h3>
        <span>1 sections</span>
        <a href='/psc/CSDPRD/EMPLOYEE/SA/c/SSR_STUDENT_FL.SSR_CS_WRAP_FL.GBL
          ?Page=SSR_CS_WRAP_FL&amp;CRSE_ID={number}&amp;CRSE_OFFER_NBR=1
          &amp;STRM=2267&amp;ACAD_CAREER={career}&amp;CLASS_NBR={number}'>View Details</a>
      </li>
    """


def _page(*, selected: str | None, capped: bool, rows: str = "") -> str:
    graduate_checked = " checked" if selected == "Graduate" else ""
    undergraduate_checked = " checked" if selected == "Undergraduate" else ""
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
          <input type='checkbox' id='PTS_SELECT$3' name='PTS_SELECT$3'
                 value='Y'{graduate_checked}>
          <label for='PTS_SELECT$3'>Graduate</label>
          <input type='checkbox' id='PTS_SELECT$4' name='PTS_SELECT$4'
                 value='Y'{undergraduate_checked}>
          <label for='PTS_SELECT$4'>Undergraduate</label>
        </fieldset>
        <div>{cap}</div><ul>{rows}</ul>
      </form></body></html>
    """


class _FailingThenResumingClient(SdsuPeopleSoftSession):
    def __init__(self, root: Path, *, mapping: dict) -> None:
        super().__init__(
            SdsuHttpConfig(
                delay_seconds=0,
                max_retries=0,
                partition_cache_dir=root,
            )
        )
        self.mapping = mapping
        self.network_paths: list[tuple[tuple[str, str], ...]] = []

    def _fetch_subject_partition_path(self, *, term, term_code, subject, filters):
        del term, term_code, subject
        self.network_paths.append(filters)
        result = self.mapping[filters]
        if isinstance(result, Exception):
            raise result
        return result, URL


def _subject_pages() -> RawSubjectPages:
    base = _page(selected=None, capped=True)
    return RawSubjectPages(
        subject="BIOL",
        term="Fall 2026",
        term_code="2267",
        search_url=URL,
        initial_html=base,
        filtered_html=base,
        exact_facet_applied=True,
    )


def _key_kwargs(filters=GRAD):
    return dict(term="Fall 2026", term_code="2267", subject="BIOL", filters=filters)


def test_crash_after_one_branch_then_resume_without_refetching_it(tmp_path: Path) -> None:
    graduate = _page(selected="Graduate", capped=False, rows=_row("600", "GRAD"))
    undergraduate = _page(selected="Undergraduate", capped=False, rows=_row("100", "UGRD"))
    failure = PeopleSoftTransientError(method="POST", url=URL)
    first = _FailingThenResumingClient(
        tmp_path, mapping={GRAD: graduate, UNDERGRAD: failure}
    )
    with pytest.raises(PeopleSoftTransientError):
        first._partition_capped_subject_pages(_subject_pages())
    assert first.network_paths == [GRAD, UNDERGRAD]
    assert list(tmp_path.rglob("*.json.gz"))  # Graduate was flushed before failure.

    # This is a completely new client/session, simulating a later --resume run.
    second = _FailingThenResumingClient(
        tmp_path, mapping={UNDERGRAD: undergraduate}
    )
    partition_pages = second._partition_capped_subject_pages(_subject_pages())
    assert second.network_paths == [UNDERGRAD]
    assert len(partition_pages) == 2
    assert all(not page.capped for page in partition_pages)
    assert {page.filters for page in partition_pages} == {GRAD, UNDERGRAD}
    # Partition-level completion must be explicit and uncapped.
    assert {p.result_count for p in partition_pages} == {1}



def test_cache_miss_on_corruption_and_wrong_identity(tmp_path: Path) -> None:
    cache = PartitionBranchCache(tmp_path)
    html = _page(selected="Graduate", capped=False)
    cache.save(**_key_kwargs(), html=html, url=URL, row_count=0, capped=False)
    validate = lambda h, u, r, c: None
    assert cache.load(**_key_kwargs(), validate=validate) is not None
    assert cache.load(**_key_kwargs(UNDERGRAD), validate=validate) is None
    assert cache.load(term="Spring 2027", term_code="2273", subject="BIOL", filters=GRAD,
                      validate=validate) is None
    cache_file = next(tmp_path.rglob("*.json.gz"))
    cache_file.write_bytes(b"corrupt")
    assert cache.load(**_key_kwargs(), validate=validate) is None


def test_cache_ttl_and_semantic_revalidation(tmp_path: Path) -> None:
    cache = PartitionBranchCache(tmp_path, max_age_seconds=10)
    html = _page(selected="Graduate", capped=False)
    cache.save(**_key_kwargs(), html=html, url=URL, row_count=0, capped=False)
    def fail_validation(html, url, rows, capped):
        raise ValueError("Wrong subject")
    assert cache.load(**_key_kwargs(), validate=fail_validation) is None
    cache_file = next(tmp_path.rglob("*.json.gz"))
    with gzip.open(cache_file, "rb") as handle:
        document = json.loads(handle.read())
    document["created_at"] = time.time() - 1000
    cache_file.write_bytes(gzip.compress(json.dumps(document).encode()))
    assert cache.load(**_key_kwargs(), validate=lambda *args: None) is None


def test_fresh_branch_never_replays_cached_form_state(tmp_path: Path) -> None:
    graduate = _page(selected="Graduate", capped=False)
    undergraduate = _page(selected="Undergraduate", capped=False)
    first = _FailingThenResumingClient(tmp_path, mapping={GRAD: graduate, UNDERGRAD: undergraduate})
    first._partition_capped_subject_pages(_subject_pages())
    assert first.network_paths == [GRAD, UNDERGRAD]
    second = _FailingThenResumingClient(tmp_path, mapping={})
    second._partition_capped_subject_pages(_subject_pages())
    assert second.network_paths == []
    # A new facet path, not present in the cache, must run a new logical request.
    fresh = _FailingThenResumingClient(tmp_path, mapping={UNDERGRAD: undergraduate})
    # Force the undergraduate cache to be invalid by changing its stored HTML.
    cache_file = first._partition_cache._path(**_key_kwargs(UNDERGRAD))
    cache_file.write_bytes(b"invalid")
    fresh._partition_capped_subject_pages(_subject_pages())
    assert fresh.network_paths == [UNDERGRAD]
