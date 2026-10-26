"""Persistent, administrator-confirmed SDSU class deletion history.

Only the explicit, verified removal workflow writes this file. A historical
record is a candidate for *fresh* verification, never proof that a new class
exists. Store complete original sections so future scans retain course identity
and group metadata even if the course has no remaining active sections.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

from classcatalog.models import CourseSection

SCHEMA_VERSION = 1
HISTORY_NAME = 'deleted_class_history.json'


def _identity(section: CourseSection) -> tuple[str, str]:
    return section.term_code or section.term, section.schedule_number


class DeletedClassHistory:
    def __init__(self, data_path: Path) -> None:
        self.path = data_path.parent / HISTORY_NAME
        self._lock = RLock()

    def _read(self) -> dict[str, object]:
        if not self.path.exists():
            return {'schema_version': SCHEMA_VERSION, 'deletions': []}
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ValueError(f'Deleted class history cannot be read: {exc}') from exc
        if (not isinstance(payload, dict) or payload.get('schema_version') != SCHEMA_VERSION
                or not isinstance(payload.get('deletions'), list)):
            raise ValueError('Deleted class history has an unsupported or invalid schema.')
        return payload

    def records(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(dict(row) for row in self._read()['deletions'])

    def sources(self, term: str = '', course_code: str = '') -> tuple[CourseSection, ...]:
        """Latest confirmed deletion for each (term, physical class number)."""
        found: dict[tuple[str, str], CourseSection] = {}
        for item in self.records():
            if not isinstance(item, dict) or not isinstance(item.get('sections'), list):
                raise ValueError('A deleted class history record is malformed.')
            try:
                sections = [CourseSection.model_validate(s) for s in item['sections']]
            except Exception as exc:
                raise ValueError('A deleted class history section is invalid.') from exc
            if not sections or len({_identity(s) for s in sections}) != 1:
                raise ValueError('A deleted class history entry has mixed physical identities.')
            section = sections[0]
            if (term and section.term != term) or (course_code and section.course_code != course_code):
                continue
            found[_identity(section)] = section
        return tuple(found.values())

    def contains(self, section: CourseSection) -> bool:
        # Exact recorded identity is required to prevent treating arbitrary class
        # records as historical proof. Seat timestamps do not affect this identity.
        for saved in self.sources(section.term, section.course_code):
            if (_identity(saved) == _identity(section) and saved.id == section.id
                    and saved.crse_id == section.crse_id
                    and saved.acad_career == section.acad_career
                    and saved.source_url == section.source_url):
                return True
        return False

    def append_confirmed(self, sections: list[CourseSection], backup: Path) -> str:
        if not sections or len({_identity(s) for s in sections}) != 1:
            raise ValueError('A confirmed physical section group is required.')
        if not backup.is_file():
            raise ValueError('Deletion backup is missing; history was not recorded.')
        with self._lock:
            payload = self._read()
            entry_id = uuid4().hex
            item = {
                'id': entry_id,
                'deleted_at': datetime.now(timezone.utc).isoformat(),
                'backup_path': str(backup),
                'key': f'{_identity(sections[0])[0]}::{sections[0].schedule_number}',
                'sections': [s.model_dump(mode='json') for s in sections],
            }
            payload['deletions'].append(item)
            self._write(payload)
            return entry_id

    def undo_pending(self, entry_id: str) -> None:
        """Compensating rollback when the dataset write fails before commit."""
        with self._lock:
            payload = self._read()
            items = payload['deletions']
            if not items or items[-1].get('id') != entry_id:
                raise ValueError('Deletion history changed during rollback; manual recovery required.')
            items.pop()
            self._write(payload)

    def _write(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f'{self.path.name}.{uuid4().hex}.tmp')
        try:
            with temp.open('x', encoding='utf-8', newline='\n') as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)
