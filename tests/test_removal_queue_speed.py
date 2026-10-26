"""Offline regression tests: automatic queue works through eligible candidates promptly."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from classcatalog.models import CourseSection
from classcatalog.removal_review import (
    AUTO_CHECKS_PER_CYCLE, AUTO_CYCLE_SECONDS, AUTO_INITIAL_SECONDS,
    RemovalReviewService, _key,
)
from classcatalog.repository import CourseRepository, SAMPLE_DATA_PATH


def _fixture(tmp_path: Path, count: int = 252):
    example = CourseRepository.from_json(
        SAMPLE_DATA_PATH, catalog_path=None, ratings_path=None
    ).sections[0]
    stale = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    sections = tuple(example.model_copy(update={
        'id': f'queue-{index}', 'term': 'Spring 2027', 'term_code': '2273',
        'course_code': 'LATAM 797', 'subject': 'LATAM', 'catalog_number': '797',
        'schedule_number': str(40000 + index), 'seat_updated_at': stale,
    }) for index in range(count))
    data = tmp_path / 'sections.json'
    data.write_text(json.dumps([s.model_dump(mode='json') for s in sections]), encoding='utf-8')
    return sections, data, CourseRepository(sections)


def test_auto_checks_one_every_two_minutes():
    assert AUTO_CYCLE_SECONDS == 120
    assert AUTO_INITIAL_SECONDS == 30
    assert AUTO_CHECKS_PER_CYCLE == 1


def test_queue_not_limited_to_first_250(tmp_path: Path):
    sections, data, repo = _fixture(tmp_path)
    calls = []
    service = RemovalReviewService(repo, data, probe=lambda section: calls.append(section.schedule_number) or False)
    assert service.candidates()['total_flagged'] == len(sections)
    assert len(service.candidates()['items']) == 250  # Public dashboard remains bounded.
    assert len(service.candidates(limit=None)['items']) == len(sections)
    # The auto worker must reach section 251 and 252, even if the first 250
    # were already checked and remain on the flagged list.
    for section in sections[:-2]:
        service._auto_attempts[_key(section)] = float('inf')
    result = service.auto_cycle(max_checks=2)
    assert result == {'checked': 2, 'hidden': 0}
    assert set(calls) == {sections[-2].schedule_number, sections[-1].schedule_number}
    assert len(repo.sections) == len(sections)
    assert data.is_file()
    assert not (tmp_path / 'hidden_classes.json').exists()


def test_requires_48_hours_and_double_confirmation(tmp_path: Path):
    sections, data, repo = _fixture(tmp_path, count=2)
    recent = sections[0].model_copy(update={
        'seat_updated_at': (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    })
    repo = CourseRepository((recent, sections[1]))
    calls = []
    service = RemovalReviewService(repo, data, probe=lambda section: calls.append(section.schedule_number) or True)
    before = data.read_bytes()
    result = service.auto_cycle()
    assert result == {'checked': 1, 'hidden': 1}
    assert calls == [sections[1].schedule_number, sections[1].schedule_number]
    assert data.read_bytes() == before
    assert (tmp_path / 'hidden_classes.json').is_file()
    assert len(repo.sections) == 1
