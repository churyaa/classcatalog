from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any

import pytest

from classcatalog.scraping import main as scraper_main
from classcatalog.scraping.main import (
    DetailScrapeOutcome,
    DetailScrapeStats,
    _build_parser,
    _load_subject_outputs_from_checkpoint,
    _new_checkpoint,
    _progress_summary,
    _resume_has_pending_detail_targets,
    _save_detail_fixtures_resumable,
    _subject_output_path,
    _validate_resume_checkpoint,
)
from classcatalog.scraping.models import (
    CourseDetailOutput,
    CourseSearchHit,
    DetailCourseStatus,
    ScrapeCheckpoint,
    SubjectCheckpointStatus,
    SubjectDetailOutput,
    SubjectScrapeError,
    SubjectScrapeResult,
)
from classcatalog.scraping.progress import (
    build_subject_output,
    checkpoint_state_from_subject_output,
    course_detail_key,
    read_model,
    refresh_subject_output,
    replace_course_detail,
    subject_output_requires_search_refresh,
    write_model,
)
from classcatalog.scraping.parser import GroupletUrlNotFound
from classcatalog.scraping.session import (
    PeopleSoftCircuitBreakerOpen,
    RawSubjectPages,
)


class _Logger:
    def info(self, event: str, **values: object) -> None:
        del event, values

    def warning(self, event: str, **values: object) -> None:
        del event, values

    def error(self, event: str, **values: object) -> None:
        del event, values


def _hit(subject: str, catalog: str, *, row: int = 0) -> CourseSearchHit:
    return CourseSearchHit(
        term="Fall 2026",
        term_code="2267",
        subject=subject,
        catalog_number=catalog,
        course_code=f"{subject} {catalog}",
        title=f"Course {catalog}",
        detail_url=f"https://example.test/{subject}/{catalog}",
        crse_id=f"{subject}-{catalog}",
        crse_offer_nbr="1",
        acad_career="UGRD",
        class_number=str(1000 + row),
        source_row_index=row,
        raw_text=f"{subject} {catalog}",
    )


def _result(subject: str, *hits: CourseSearchHit) -> SubjectScrapeResult:
    return SubjectScrapeResult(
        term="Fall 2026",
        term_code="2267",
        subject=subject,
        fetched_at="2026-08-20T00:00:00+00:00",
        search_url=f"https://example.test/search/{subject}",
        initial_result_count=len(hits),
        filtered_result_count=len(hits),
        exact_facet_applied=True,
        initial_result_cap_warning=False,
        filtered_result_cap_warning=False,
        complete=True,
        courses=hits,
    )


def _complete_detail(hit: CourseSearchHit) -> CourseDetailOutput:
    return CourseDetailOutput(
        course_key=course_detail_key(hit),
        course=hit,
        status=DetailCourseStatus.COMPLETE,
        started_at="2026-08-20T00:00:00+00:00",
        updated_at="2026-08-20T00:01:00+00:00",
        completed_at="2026-08-20T00:01:00+00:00",
    )


def test_subject_output_becomes_complete_after_all_target_courses_finish() -> None:
    first = _hit("CS", "150", row=1)
    second = _hit("CS", "160", row=2)
    output = build_subject_output(
        run_id="run-1",
        started_at="2026-08-20T00:00:00+00:00",
        detail_limit=2,
        result=_result("CS", first, second),
    )

    assert output.complete is False
    assert output.detail_courses_targeted == 2

    output = replace_course_detail(output, _complete_detail(first))
    assert output.complete is False
    assert output.detail_courses_complete == 1

    output = replace_course_detail(output, _complete_detail(second))
    assert output.complete is True
    assert output.detail_courses_attempted == 2
    assert output.detail_courses_complete == 2
    assert output.detail_courses_partial == 0

    state = checkpoint_state_from_subject_output(
        output,
        output_path=Path("results/fall-2026/subjects/cs.json"),
    )
    assert state.status is SubjectCheckpointStatus.COMPLETE
    assert state.completed_course_keys == (
        course_detail_key(first),
        course_detail_key(second),
    )


def test_deep_subject_with_courses_and_no_targets_cannot_remain_complete() -> None:
    missing_url = _hit("BIOMI", "500").model_copy(update={"detail_url": None})
    result = _result("BIOMI", missing_url)
    stale = SubjectDetailOutput(
        run_id="run-1",
        started_at="2026-08-20T00:00:00+00:00",
        updated_at="2026-08-20T00:01:00+00:00",
        completed_at="2026-08-20T00:01:00+00:00",
        term="Fall 2026",
        term_code="2267",
        subject="BIOMI",
        detail_limit=9999,
        search_result=result,
        complete=True,
    )

    refreshed = refresh_subject_output(stale)

    assert refreshed.complete is False
    assert refreshed.detail_courses_targeted == 1
    assert refreshed.detail_courses_attempted == 0
    assert subject_output_requires_search_refresh(refreshed) is True


def test_progress_summary_counts_only_fully_complete_subjects() -> None:
    complete_output = build_subject_output(
        run_id="run-1",
        started_at="2026-08-20T00:00:00+00:00",
        detail_limit=0,
        result=_result("CS", _hit("CS", "150")),
    )
    incomplete_output = build_subject_output(
        run_id="run-1",
        started_at="2026-08-20T00:00:00+00:00",
        detail_limit=1,
        result=_result("MATH", _hit("MATH", "110")),
    )

    summary = _progress_summary(
        ("CS", "MATH"),
        {"CS": complete_output, "MATH": incomplete_output},
        {},
    )

    assert summary.completed_subjects == ("CS",)
    assert summary.incomplete_subjects == 1


def test_resume_validation_requires_same_run_shape(tmp_path: Path) -> None:
    output_path = tmp_path / "aggregate.json"
    subject_dir = tmp_path / "subjects"
    checkpoint = _new_checkpoint(
        term="Fall 2026",
        term_code="2267",
        requested_subjects=("CS", "MATH"),
        detail_limit=25,
        output_path=output_path,
        subject_output_dir=subject_dir,
    )

    _validate_resume_checkpoint(
        checkpoint,
        term="Fall 2026",
        term_code="2267",
        requested_subjects=("CS", "MATH"),
        detail_limit=25,
        output_path=output_path,
        subject_output_dir=subject_dir,
    )

    with pytest.raises(ValueError, match="detail limit"):
        _validate_resume_checkpoint(
            checkpoint,
            term="Fall 2026",
            term_code="2267",
            requested_subjects=("CS", "MATH"),
            detail_limit=10,
            output_path=output_path,
            subject_output_dir=subject_dir,
        )


def test_resumable_detail_scrape_skips_complete_and_retries_partial(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = _hit("CS", "150", row=1)
    second = _hit("CS", "160", row=2)
    complete = _complete_detail(first)
    partial = CourseDetailOutput(
        course_key=course_detail_key(second),
        course=second,
        status=DetailCourseStatus.PARTIAL,
        started_at="2026-08-20T00:00:00+00:00",
        updated_at="2026-08-20T00:01:00+00:00",
        completed_at="2026-08-20T00:01:00+00:00",
        attempts=1,
        warning_events=("detail_warning",),
    )
    calls: list[str] = []

    def fake_detail_scrape(
        client: object,
        result: SubjectScrapeResult,
        *,
        fixture_root: Path,
        limit: int,
        logger: object,
    ) -> DetailScrapeStats:
        del client, fixture_root, limit, logger
        calls.append(result.courses[0].course_code)
        return DetailScrapeStats(attempted=1, complete=1)

    monkeypatch.setattr(scraper_main, "_save_detail_fixtures", fake_detail_scrape)
    monkeypatch.setattr(
        scraper_main,
        "_read_course_detail_fixtures",
        lambda *args, **kwargs: (None, (), ()),
    )

    progress: list[CourseDetailOutput] = []
    outcome = _save_detail_fixtures_resumable(
        object(),  # type: ignore[arg-type]
        _result("CS", first, second),
        fixture_root=tmp_path,
        limit=2,
        logger=_Logger(),  # type: ignore[arg-type]
        existing=(complete, partial),
        progress_callback=progress.append,
    )

    assert calls == ["CS 160"]
    assert outcome.stats.attempted == 2
    assert outcome.stats.complete == 2
    assert outcome.stats.partial == 0
    retried = next(record for record in outcome.courses if record.course.course_code == "CS 160")
    assert retried.attempts == 2
    assert retried.status is DetailCourseStatus.COMPLETE
    assert [record.status for record in progress] == [
        DetailCourseStatus.IN_PROGRESS,
        DetailCourseStatus.COMPLETE,
    ]


def test_resumable_detail_scrape_rehydrates_and_retries_state_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hit = _hit("CS", "150", row=1)
    calls = 0

    def fake_detail_scrape(
        client: object,
        result: SubjectScrapeResult,
        *,
        fixture_root: Path,
        limit: int,
        logger: object,
    ) -> DetailScrapeStats:
        nonlocal calls
        del client, result, fixture_root, limit, logger
        calls += 1
        if calls == 1:
            raise GroupletUrlNotFound("missing detail grouplet")
        return DetailScrapeStats(attempted=1, complete=1)

    class _Config:
        in_run_state_recovery_attempts = 1

    class _Client:
        config = _Config()

        def __init__(self) -> None:
            self.rehydrate_calls = 0
            self.successes = 0
            self.failures = 0

        def rehydrate_subject_context(self, **kwargs: object) -> RawSubjectPages:
            assert kwargs["mark_success"] is False
            self.rehydrate_calls += 1
            return RawSubjectPages(
                subject="CS",
                term="Fall 2026",
                term_code="2267",
                search_url="https://example.test/search/CS",
                initial_html="<html></html>",
                filtered_html="<html></html>",
                exact_facet_applied=True,
            )

        def record_state_success(self) -> None:
            self.successes += 1

        def record_state_failure(self, **kwargs: object) -> None:
            del kwargs
            self.failures += 1

    monkeypatch.setattr(scraper_main, "_save_detail_fixtures", fake_detail_scrape)
    monkeypatch.setattr(
        scraper_main,
        "_read_course_detail_fixtures",
        lambda *args, **kwargs: (None, (), ()),
    )
    client = _Client()

    outcome = _save_detail_fixtures_resumable(
        client,  # type: ignore[arg-type]
        _result("CS", hit),
        fixture_root=tmp_path,
        limit=1,
        logger=_Logger(),  # type: ignore[arg-type]
    )

    assert calls == 2
    assert client.rehydrate_calls == 1
    assert client.successes == 1
    assert client.failures == 0
    assert outcome.stats.complete == 1
    assert outcome.courses[0].status is DetailCourseStatus.COMPLETE



def test_resume_reconciles_stale_checkpoint_from_newer_subject_file(
    tmp_path: Path,
) -> None:
    subject_dir = tmp_path / "subjects"
    output_path = tmp_path / "aggregate.json"
    checkpoint = _new_checkpoint(
        term="Fall 2026",
        term_code="2267",
        requested_subjects=("CS",),
        detail_limit=0,
        output_path=output_path,
        subject_output_dir=subject_dir,
    )
    subject_output = build_subject_output(
        run_id=checkpoint.run_id,
        started_at=checkpoint.started_at,
        detail_limit=0,
        result=_result("CS", _hit("CS", "150")),
    )
    subject_path = _subject_output_path(subject_dir, "CS")
    write_model(subject_path, subject_output)
    stale_error = SubjectScrapeError(
        term="Fall 2026",
        subject="CS",
        error_type="RuntimeError",
        message="stale checkpoint error",
    )
    stale_state = checkpoint.subjects[0].model_copy(
        update={
            "status": SubjectCheckpointStatus.FAILED,
            "error": stale_error,
        }
    )
    checkpoint = checkpoint.model_copy(update={"subjects": (stale_state,)})

    outputs, reconciled = _load_subject_outputs_from_checkpoint(
        checkpoint,
        logger=_Logger(),  # type: ignore[arg-type]
    )

    assert outputs["CS"].complete is True
    assert reconciled.subjects[0].status is SubjectCheckpointStatus.COMPLETE
    assert reconciled.subjects[0].error is None


def test_resume_reopens_false_complete_deep_subject_for_search_refresh(
    tmp_path: Path,
) -> None:
    subject_dir = tmp_path / "subjects"
    output_path = tmp_path / "aggregate.json"
    checkpoint = _new_checkpoint(
        term="Fall 2026",
        term_code="2267",
        requested_subjects=("BIOMI",),
        detail_limit=9999,
        output_path=output_path,
        subject_output_dir=subject_dir,
    )
    missing_url = _hit("BIOMI", "500").model_copy(update={"detail_url": None})
    stale_output = SubjectDetailOutput(
        run_id=checkpoint.run_id,
        started_at=checkpoint.started_at,
        updated_at="2026-08-20T00:01:00+00:00",
        completed_at="2026-08-20T00:01:00+00:00",
        term="Fall 2026",
        term_code="2267",
        subject="BIOMI",
        detail_limit=9999,
        search_result=_result("BIOMI", missing_url),
        complete=True,
    )
    subject_path = _subject_output_path(subject_dir, "BIOMI")
    write_model(subject_path, stale_output)
    stale_state = checkpoint_state_from_subject_output(
        stale_output,
        output_path=subject_path,
        force_status=SubjectCheckpointStatus.COMPLETE,
    )
    checkpoint = checkpoint.model_copy(update={"subjects": (stale_state,)})

    outputs, reconciled = _load_subject_outputs_from_checkpoint(
        checkpoint,
        logger=_Logger(),  # type: ignore[arg-type]
    )

    assert outputs["BIOMI"].complete is False
    assert subject_output_requires_search_refresh(outputs["BIOMI"]) is True
    assert reconciled.subjects[0].status is SubjectCheckpointStatus.PENDING
    assert reconciled.subjects[0].error is None

def test_run_writes_checkpoint_subject_outputs_and_resumes_without_search(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    output_path = tmp_path / "aggregate.json"
    subject_dir = tmp_path / "subjects"
    hits = {subject: _hit(subject, "100") for subject in ("CS", "MATH")}

    class FakeSession:
        scrape_calls: list[str] = []
        forbid_scrape = False

        def __init__(self, config: object) -> None:
            del config

        def __enter__(self) -> FakeSession:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            del exc_type, exc, traceback

        def resolve_term_code(self, term: str, override: str | None) -> str:
            assert term == "Fall 2026"
            return override or "2267"

        def scrape_subject(self, **kwargs: Any) -> tuple[RawSubjectPages, SubjectScrapeResult]:
            subject = str(kwargs["subject"])
            if self.forbid_scrape:
                raise AssertionError("resume should not re-scrape a completed subject")
            self.scrape_calls.append(subject)
            result = _result(subject, hits[subject])
            pages = RawSubjectPages(
                subject=subject,
                term="Fall 2026",
                term_code="2267",
                search_url=result.search_url,
                initial_html="<html></html>",
                filtered_html="<html></html>",
                exact_facet_applied=True,
            )
            return pages, result

    monkeypatch.setattr(scraper_main, "SdsuPeopleSoftSession", FakeSession)
    parser = _build_parser()
    fresh_args = parser.parse_args(
        [
            "--term",
            "Fall 2026",
            "--term-code",
            "2267",
            "--subjects",
            "CS",
            "MATH",
            "--detail-limit",
            "0",
            "--checkpoint",
            str(checkpoint_path),
            "--output",
            str(output_path),
            "--subject-output-dir",
            str(subject_dir),
        ]
    )

    assert scraper_main.run(fresh_args) == 0
    assert FakeSession.scrape_calls == ["CS", "MATH"]
    checkpoint = read_model(checkpoint_path, ScrapeCheckpoint)
    assert checkpoint.completed_at is not None
    assert all(state.status is SubjectCheckpointStatus.COMPLETE for state in checkpoint.subjects)
    assert _subject_output_path(subject_dir, "CS").exists()
    assert _subject_output_path(subject_dir, "MATH").exists()

    FakeSession.scrape_calls.clear()
    FakeSession.forbid_scrape = True
    resume_args = parser.parse_args(
        [
            "--term",
            "Fall 2026",
            "--term-code",
            "2267",
            "--resume",
            "--checkpoint",
            str(checkpoint_path),
        ]
    )
    assert scraper_main.run(resume_args) == 0
    assert FakeSession.scrape_calls == []

    cs_output = read_model(_subject_output_path(subject_dir, "CS"), SubjectDetailOutput)
    assert cs_output.complete is True
    aggregate = output_path.read_text(encoding="utf-8")
    assert '"completed_subjects": [' in aggregate


def test_resume_rebuilds_false_complete_search_instead_of_skipping_subject(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    output_path = tmp_path / "aggregate.json"
    subject_dir = tmp_path / "subjects"
    valid_hit = _hit("BIOMI", "500")
    stale_hit = valid_hit.model_copy(update={"detail_url": None})
    checkpoint = _new_checkpoint(
        term="Fall 2026",
        term_code="2267",
        requested_subjects=("BIOMI",),
        detail_limit=1,
        output_path=output_path,
        subject_output_dir=subject_dir,
    )
    stale_output = SubjectDetailOutput(
        run_id=checkpoint.run_id,
        started_at=checkpoint.started_at,
        updated_at="2026-08-20T00:01:00+00:00",
        completed_at="2026-08-20T00:01:00+00:00",
        term="Fall 2026",
        term_code="2267",
        subject="BIOMI",
        detail_limit=1,
        search_result=_result("BIOMI", stale_hit),
        complete=True,
    )
    subject_path = _subject_output_path(subject_dir, "BIOMI")
    write_model(subject_path, stale_output)
    checkpoint = checkpoint.model_copy(
        update={
            "subjects": (
                checkpoint_state_from_subject_output(
                    stale_output,
                    output_path=subject_path,
                    force_status=SubjectCheckpointStatus.COMPLETE,
                ),
            )
        }
    )
    write_model(checkpoint_path, checkpoint)

    class FakeSession:
        scrape_calls: list[str] = []

        def __init__(self, config: object) -> None:
            del config

        def __enter__(self) -> "FakeSession":
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            del exc_type, exc, traceback

        def resolve_term_code(self, term: str, override: str | None) -> str:
            assert term == "Fall 2026"
            return override or "2267"

        def scrape_subject(self, **kwargs: Any) -> tuple[RawSubjectPages, SubjectScrapeResult]:
            assert kwargs["require_detail_urls"] is True
            subject = str(kwargs["subject"])
            self.scrape_calls.append(subject)
            result = _result(subject, valid_hit)
            return (
                RawSubjectPages(
                    subject=subject,
                    term="Fall 2026",
                    term_code="2267",
                    search_url=result.search_url,
                    initial_html="<html></html>",
                    filtered_html="<html></html>",
                    exact_facet_applied=True,
                ),
                result,
            )

    def fake_resumable(
        client: object,
        result: SubjectScrapeResult,
        *,
        fixture_root: Path,
        limit: int,
        logger: object,
        existing: tuple[CourseDetailOutput, ...] = (),
        progress_callback: object | None = None,
    ) -> DetailScrapeOutcome:
        del client, fixture_root, logger, existing, progress_callback
        assert limit == 1
        completed = _complete_detail(result.courses[0])
        return DetailScrapeOutcome(
            stats=DetailScrapeStats(attempted=1, complete=1),
            courses=(completed,),
        )

    monkeypatch.setattr(scraper_main, "SdsuPeopleSoftSession", FakeSession)
    monkeypatch.setattr(scraper_main, "_save_detail_fixtures_resumable", fake_resumable)

    args = _build_parser().parse_args(
        [
            "--term",
            "Fall 2026",
            "--term-code",
            "2267",
            "--resume",
            "--checkpoint",
            str(checkpoint_path),
        ]
    )

    assert scraper_main.run(args) == 0
    assert FakeSession.scrape_calls == ["BIOMI"]
    refreshed = read_model(subject_path, SubjectDetailOutput)
    assert refreshed.complete is True
    assert refreshed.detail_courses_complete == 1


def test_deep_run_rehydrates_partition_context_before_course_details(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    output_path = tmp_path / "aggregate.json"
    subject_dir = tmp_path / "subjects"
    hit = _hit("ART", "100")
    partitioned_result = _result("ART", hit).model_copy(
        update={
            "partitioned": True,
            "partition_facets": ("Course Career", "Class Status"),
            "partition_leaf_count": 4,
        }
    )

    class FakeSession:
        prepare_calls: list[str] = []

        def __init__(self, config: object) -> None:
            del config

        def __enter__(self) -> "FakeSession":
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            del exc_type, exc, traceback

        def resolve_term_code(self, term: str, override: str | None) -> str:
            assert term == "Fall 2026"
            return override or "2267"

        def scrape_subject(self, **kwargs: Any) -> tuple[RawSubjectPages, SubjectScrapeResult]:
            assert kwargs["require_detail_urls"] is True
            return (
                RawSubjectPages(
                    subject="ART",
                    term="Fall 2026",
                    term_code="2267",
                    search_url=partitioned_result.search_url,
                    initial_html="<html></html>",
                    filtered_html="<html></html>",
                    exact_facet_applied=True,
                ),
                partitioned_result,
            )

        def prepare_partitioned_detail_result(
            self,
            result: SubjectScrapeResult,
        ) -> SubjectScrapeResult:
            self.prepare_calls.append(result.subject)
            return result

    def fake_resumable(
        client: object,
        result: SubjectScrapeResult,
        *,
        fixture_root: Path,
        limit: int,
        logger: object,
        existing: tuple[CourseDetailOutput, ...] = (),
        progress_callback: object | None = None,
    ) -> DetailScrapeOutcome:
        del client, fixture_root, logger, existing, progress_callback
        assert FakeSession.prepare_calls == ["ART"]
        assert limit == 1
        completed = _complete_detail(result.courses[0])
        return DetailScrapeOutcome(
            stats=DetailScrapeStats(attempted=1, complete=1),
            courses=(completed,),
        )

    monkeypatch.setattr(scraper_main, "SdsuPeopleSoftSession", FakeSession)
    monkeypatch.setattr(scraper_main, "_save_detail_fixtures_resumable", fake_resumable)

    args = _build_parser().parse_args(
        [
            "--term",
            "Fall 2026",
            "--term-code",
            "2267",
            "--subjects",
            "ART",
            "--detail-limit",
            "1",
            "--checkpoint",
            str(checkpoint_path),
            "--output",
            str(output_path),
            "--subject-output-dir",
            str(subject_dir),
        ]
    )

    assert scraper_main.run(args) == 0
    assert FakeSession.prepare_calls == ["ART"]



def test_resume_pending_detail_targets_only_requires_context_for_unfinished_work() -> None:
    first = _hit("CS", "150", row=1)
    second = _hit("CS", "160", row=2)
    result = _result("CS", first, second)
    complete = _complete_detail(first)
    partial = CourseDetailOutput(
        course_key=course_detail_key(second),
        course=second,
        status=DetailCourseStatus.PARTIAL,
        started_at="2026-08-20T00:00:00+00:00",
        updated_at="2026-08-20T00:01:00+00:00",
        completed_at="2026-08-20T00:01:00+00:00",
    )

    assert _resume_has_pending_detail_targets(
        result,
        limit=2,
        existing=(complete, partial),
    ) is True
    assert _resume_has_pending_detail_targets(
        result,
        limit=1,
        existing=(complete, partial),
    ) is False
    assert _resume_has_pending_detail_targets(
        result,
        limit=0,
        existing=(complete, partial),
    ) is False


def test_resume_rehydrates_people_soft_context_before_retrying_details(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    output_path = tmp_path / "aggregate.json"
    subject_dir = tmp_path / "subjects"
    first = _hit("CS", "150", row=1)
    second = _hit("CS", "160", row=2)
    result = _result("CS", first, second)

    checkpoint = _new_checkpoint(
        term="Fall 2026",
        term_code="2267",
        requested_subjects=("CS",),
        detail_limit=2,
        output_path=output_path,
        subject_output_dir=subject_dir,
    )
    subject_output = build_subject_output(
        run_id=checkpoint.run_id,
        started_at=checkpoint.started_at,
        detail_limit=2,
        result=result,
    )
    subject_output = replace_course_detail(subject_output, _complete_detail(first))
    partial = CourseDetailOutput(
        course_key=course_detail_key(second),
        course=second,
        status=DetailCourseStatus.PARTIAL,
        started_at="2026-08-20T00:00:00+00:00",
        updated_at="2026-08-20T00:01:00+00:00",
        completed_at="2026-08-20T00:01:00+00:00",
        attempts=1,
    )
    subject_output = replace_course_detail(subject_output, partial)
    subject_path = _subject_output_path(subject_dir, "CS")
    write_model(subject_path, subject_output)
    checkpoint = checkpoint.model_copy(
        update={
            "subjects": (
                checkpoint_state_from_subject_output(
                    subject_output,
                    output_path=subject_path,
                ),
            )
        }
    )
    write_model(checkpoint_path, checkpoint)

    class FakeSession:
        rehydrate_calls: list[str] = []
        scrape_calls: list[str] = []

        def __init__(self, config: object) -> None:
            del config

        def __enter__(self) -> "FakeSession":
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            del exc_type, exc, traceback

        def resolve_term_code(self, term: str, override: str | None) -> str:
            assert term == "Fall 2026"
            return override or "2267"

        def scrape_subject(self, **kwargs: Any) -> tuple[RawSubjectPages, SubjectScrapeResult]:
            self.scrape_calls.append(str(kwargs["subject"]))
            raise AssertionError("resume should reuse the persisted complete search result")

        def rehydrate_subject_context(self, **kwargs: Any) -> RawSubjectPages:
            subject = str(kwargs["subject"])
            self.rehydrate_calls.append(subject)
            return RawSubjectPages(
                subject=subject,
                term="Fall 2026",
                term_code="2267",
                search_url=result.search_url,
                initial_html="<html></html>",
                filtered_html="<html></html>",
                exact_facet_applied=True,
            )

    def fake_resumable(
        client: object,
        resumed_result: SubjectScrapeResult,
        *,
        fixture_root: Path,
        limit: int,
        logger: object,
        existing: tuple[CourseDetailOutput, ...] = (),
        progress_callback: object | None = None,
    ) -> DetailScrapeOutcome:
        del client, fixture_root, logger, progress_callback
        assert FakeSession.rehydrate_calls == ["CS"]
        assert resumed_result == result
        assert limit == 2
        prior = {record.course_key: record for record in existing}
        retried = _complete_detail(second).model_copy(update={"attempts": 2})
        return DetailScrapeOutcome(
            stats=DetailScrapeStats(attempted=2, complete=2),
            courses=(prior[course_detail_key(first)], retried),
        )

    monkeypatch.setattr(scraper_main, "SdsuPeopleSoftSession", FakeSession)
    monkeypatch.setattr(scraper_main, "_save_detail_fixtures_resumable", fake_resumable)

    args = _build_parser().parse_args(
        [
            "--term",
            "Fall 2026",
            "--term-code",
            "2267",
            "--resume",
            "--checkpoint",
            str(checkpoint_path),
        ]
    )

    assert scraper_main.run(args) == 0
    assert FakeSession.rehydrate_calls == ["CS"]
    assert FakeSession.scrape_calls == []
    resumed_output = read_model(subject_path, SubjectDetailOutput)
    assert resumed_output.complete is True
    assert resumed_output.detail_courses_complete == 2


def test_run_stops_remaining_subjects_when_state_circuit_breaker_opens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    output_path = tmp_path / "aggregate.json"
    subject_dir = tmp_path / "subjects"

    class FakeSession:
        scrape_calls: list[str] = []

        def __init__(self, config: object) -> None:
            del config

        def __enter__(self) -> "FakeSession":
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            del exc_type, exc, traceback

        def resolve_term_code(self, term: str, override: str | None) -> str:
            assert term == "Fall 2026"
            return override or "2267"

        def scrape_subject(self, **kwargs: Any) -> tuple[RawSubjectPages, SubjectScrapeResult]:
            subject = str(kwargs["subject"])
            self.scrape_calls.append(subject)
            raise PeopleSoftCircuitBreakerOpen("invalid PeopleSoft state")

    monkeypatch.setattr(scraper_main, "SdsuPeopleSoftSession", FakeSession)
    args = _build_parser().parse_args(
        [
            "--term",
            "Fall 2026",
            "--term-code",
            "2267",
            "--subjects",
            "CS",
            "MATH",
            "--detail-limit",
            "0",
            "--checkpoint",
            str(checkpoint_path),
            "--output",
            str(output_path),
            "--subject-output-dir",
            str(subject_dir),
        ]
    )

    assert scraper_main.run(args) == 1
    assert FakeSession.scrape_calls == ["CS"]
    checkpoint = read_model(checkpoint_path, ScrapeCheckpoint)
    assert checkpoint.subjects[0].status is SubjectCheckpointStatus.FAILED
    assert checkpoint.subjects[1].status is SubjectCheckpointStatus.PENDING
