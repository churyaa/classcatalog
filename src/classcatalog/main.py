from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import time as time_module
from contextlib import asynccontextmanager
from datetime import datetime, time, timezone
from typing import Annotated
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from classcatalog.catalog.models import (
    CatalogProgramDetail,
    CatalogProgramListResponse,
    CatalogProgramSummary,
    CatalogRequirementDetail,
    CatalogRequirementListResponse,
    CatalogStatus,
    StudentProfileSummary,
)
from classcatalog.errors import RecentErrorStore, public_error_for_path
from classcatalog.filters import SearchFilters
from classcatalog.models import (
    GradingType,
    InstructionMode,
    ProgramClassification,
    SearchOptions,
    SearchResponse,
    SeatStatus,
    SortBy,
    Weekday,
)
from classcatalog.ratings import (
    RatingsCacheEditor,
    RmpGraphqlClient,
    canonical_instructor_name,
    is_placeholder_instructor,
    normalize_person_name,
    parse_rmp_profile_id,
)
from classcatalog.repository import (
    ACTIVE_CATALOG_PATH,
    ACTIVE_DATA_PATH,
    ACTIVE_RATINGS_PATH,
    CourseRepository,
)
from classcatalog.repository import (
    repository as default_repository,
)
from classcatalog.seats import SeatRefreshService, seat_refresh_env_enabled

STATIC_DIR = Path(__file__).parent / "static"
LOGGER = logging.getLogger(__name__)

ADMIN_PASSWORD_ENV = "CLASSCATALOG_ADMIN_PASSWORD"
ADMIN_SESSION_COOKIE = "classcatalog_admin_session"
ADMIN_SESSION_MAX_AGE_SECONDS = 60 * 60 * 8
ADMIN_LOGIN_WINDOW_SECONDS = 60 * 15
ADMIN_LOGIN_MAX_FAILURES = 5
BASE_DIR = Path(__file__).resolve().parent
ERROR_PAGES_DIR = BASE_DIR / "error_pages"

class AdminLoginRequest(BaseModel):
    password: str


class SeatInterestRequest(BaseModel):
    schedule_numbers: list[str]


class AdminSeatRefreshRequest(BaseModel):
    course_code: str


class AdminProfessorOverrideRequest(BaseModel):
    instructor_name: str = Field(min_length=1, max_length=200)
    rmp_profile: str = Field(min_length=1, max_length=500)


def _file_health(path: Path | None) -> dict[str, object]:
    if path is None:
        return {"loaded": False, "path": None, "name": None, "size_bytes": 0, "modified_at": None}
    try:
        stat = path.stat()
    except OSError:
        return {"loaded": False, "path": str(path), "name": path.name, "size_bytes": 0, "modified_at": None}
    return {
        "loaded": True,
        "path": str(path),
        "name": path.name,
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


def _ratings_sync_summary(path: Path | None) -> dict[str, object]:
    summary: dict[str, object] = {
        "generated_at": None,
        "instructors": 0,
        "matched": 0,
        "unmatched": 0,
        "errors": 0,
    }
    if path is None or not path.is_file():
        return summary
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return summary
    if not isinstance(raw, dict):
        return summary
    raw_summary = raw.get("summary")
    if not isinstance(raw_summary, dict):
        raw_summary = {}
    summary["generated_at"] = raw.get("generated_at")
    for key in ("instructors", "matched", "unmatched", "errors"):
        value = raw_summary.get(key, 0)
        try:
            summary[key] = max(0, int(value or 0))
        except (TypeError, ValueError):
            summary[key] = 0
    return summary


def _instructor_inventory(repository: CourseRepository) -> list[dict[str, object]]:
    instructors: dict[str, dict[str, object]] = {}
    for section in repository.sections:
        if is_placeholder_instructor(section.instructor):
            continue
        display_name = canonical_instructor_name(section.instructor or "")
        normalized_name = normalize_person_name(display_name)
        if not normalized_name:
            continue
        item = instructors.setdefault(
            normalized_name,
            {
                "name": display_name,
                "normalized_name": normalized_name,
                "course_codes": set(),
                "matched": False,
            },
        )
        course_codes = item["course_codes"]
        assert isinstance(course_codes, set)
        course_codes.add(section.course_code)
        item["matched"] = bool(item["matched"] or section.professor is not None)

    return [
        {
            **item,
            "course_codes": sorted(item["course_codes"]),
        }
        for item in sorted(
            instructors.values(),
            key=lambda value: str(value["name"]).casefold(),
        )
    ]


def _admin_health_snapshot(
    repository: CourseRepository,
    *,
    data_path: Path,
    catalog_path: Path | None,
    ratings_path: Path | None,
    seat_refresh_service: SeatRefreshService | None = None,
    recent_errors: list[dict[str, object]] | None = None,
    manual_rating_overrides: tuple[dict[str, object], ...] = (),
    manual_ratings_enabled: bool = False,
    manual_ratings_error: str | None = None,
) -> dict[str, object]:
    audit = repository.coverage_audit()
    catalog_status = repository.catalog.status()

    physical: dict[tuple[str, str], dict[str, bool]] = {}
    instructor_inventory = _instructor_inventory(repository)
    for section in repository.sections:
        physical_key = repository._physical_key(section)
        signals = physical.setdefault(
            physical_key,
            {"instructor": False, "location": False, "meeting": False},
        )
        if not is_placeholder_instructor(section.instructor):
            signals["instructor"] = True
        if (section.location or "").strip() or any(
            (meeting.location or "").strip() for meeting in section.meetings
        ):
            signals["location"] = True
        if section.meetings:
            signals["meeting"] = True

    missing_instructor = sum(not signals["instructor"] for signals in physical.values())
    missing_location = sum(not signals["location"] for signals in physical.values())
    missing_meetings = sum(not signals["meeting"] for signals in physical.values())
    instructor_total = len(instructor_inventory)
    rmp_matched = sum(bool(item["matched"]) for item in instructor_inventory)
    rmp_unmatched = max(0, instructor_total - rmp_matched)
    match_rate = round((rmp_matched / instructor_total * 100), 1) if instructor_total else 0.0
    unmatched_instructors = [item for item in instructor_inventory if not item["matched"]]

    ratings_summary = _ratings_sync_summary(ratings_path)
    return {
        "status": "passed" if audit.status == "passed" else "failed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "seat_refresh": (
            seat_refresh_service.admin_status()
            if seat_refresh_service is not None
            else {"enabled": False, "running": False, "refreshable_course_pages": 0, "cached_sections": 0}
        ),
        "coverage": audit.model_dump(),
        "inventory": {
            "course_section_listings": repository.total,
            "physical_sections": repository.physical_section_total,
            "displayed_options": audit.displayed_options,
            "courses": repository.course_total,
            "subjects": repository.subject_total,
            "terms": len(repository.options().terms),
        },
        "completeness": {
            "physical_sections_missing_instructor": missing_instructor,
            "physical_sections_missing_location": missing_location,
            "physical_sections_missing_meetings": missing_meetings,
        },
        "professors": {
            "instructors": instructor_total,
            "matched": rmp_matched,
            "unmatched": rmp_unmatched,
            "match_rate": match_rate,
            "cached_rating_records": repository.ratings_record_count,
            "sync": ratings_summary,
            "manual_matching_enabled": manual_ratings_enabled,
            "manual_matching_error": manual_ratings_error,
            "unmatched_instructors": unmatched_instructors,
            "manual_overrides": list(manual_rating_overrides),
        },
        "catalog": {
            "loaded": catalog_status.loaded,
            "programs": catalog_status.programs,
            "requirements": catalog_status.requirements,
        },
        "files": {
            "sections": _file_health(data_path),
            "catalog": _file_health(catalog_path),
            "ratings": _file_health(ratings_path),
        },
        "recent_errors": recent_errors or [],
    }


def create_app(
    course_repository: CourseRepository | None = None,
    *,
    data_path: Path | None = None,
    catalog_path: Path | None = None,
    ratings_path: Path | None = None,
    admin_password: str | None = None,
    seat_refresh_service: SeatRefreshService | None = None,
    seat_refresh_enabled: bool | None = None,
    error_store: RecentErrorStore | None = None,
    rmp_client: RmpGraphqlClient | None = None,
) -> FastAPI:
    """Create the API around an explicit repository for tests and data validation."""

    active_repository = course_repository or default_repository
    active_data_path = data_path or ACTIVE_DATA_PATH
    active_catalog_path = catalog_path if catalog_path is not None else ACTIVE_CATALOG_PATH
    active_ratings_path = ratings_path if ratings_path is not None else ACTIVE_RATINGS_PATH
    ratings_editor = (
        RatingsCacheEditor(active_ratings_path)
        if active_ratings_path is not None
        else None
    )
    active_rmp_client = rmp_client or RmpGraphqlClient()
    recent_error_store = error_store or RecentErrorStore()
    configured_admin_password = admin_password if admin_password is not None else os.getenv(ADMIN_PASSWORD_ENV)
    admin_enabled = bool(configured_admin_password)
    should_enable_seat_refresh = (
        seat_refresh_enabled
        if seat_refresh_enabled is not None
        else seat_refresh_env_enabled(default=True)
    )
    active_seat_service = seat_refresh_service
    if (
        active_seat_service is None
        and course_repository is None
        and should_enable_seat_refresh
    ):
        active_seat_service = SeatRefreshService(active_repository)
    admin_sessions: dict[str, float] = {}
    failed_admin_logins: dict[str, list[float]] = {}

    def _record_startup_error(path: str, detail: str | None) -> None:
        if not detail:
            return
        code, public_message = public_error_for_path(path)
        recent_error_store.record(
            request_id=secrets.token_hex(8),
            method="STARTUP",
            path=path,
            status_code=503,
            code=code,
            public_message=public_message,
            exception_type="DataLoadError",
            detail=detail,
        )

    _record_startup_error("/api/ratings/status", active_repository.ratings_load_error)
    _record_startup_error("/api/catalog/status", active_repository.catalog_load_error)

    def _client_key(request: Request) -> str:
        return request.client.host if request.client is not None else "unknown"

    def _prune_sessions(now: float) -> None:
        expired = [token for token, expires_at in admin_sessions.items() if expires_at <= now]
        for token in expired:
            admin_sessions.pop(token, None)

    def _is_admin_authenticated(request: Request) -> bool:
        if not admin_enabled:
            return False
        now = time_module.time()
        _prune_sessions(now)
        token = request.cookies.get(ADMIN_SESSION_COOKIE)
        return bool(token and admin_sessions.get(token, 0) > now)

    def _require_admin(request: Request) -> None:
        if not admin_enabled:
            raise HTTPException(status_code=404, detail="Admin access is not configured.")
        if not _is_admin_authenticated(request):
            raise HTTPException(status_code=401, detail="Admin authentication required.")

    def _require_catalog_data() -> None:
        if active_repository.catalog_load_error:
            raise HTTPException(status_code=503, detail=active_repository.catalog_load_error)

    def _secure_cookie(request: Request) -> bool:
        forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
        return request.url.scheme == "https" or forwarded_proto == "https"

    def _manual_rating_state() -> tuple[tuple[dict[str, object], ...], str | None]:
        if ratings_editor is None:
            return (), None
        try:
            return ratings_editor.manual_overrides(), None
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return (), f"{type(exc).__name__}: {exc}"

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if active_seat_service is not None:
            active_seat_service.start()
        try:
            yield
        finally:
            if active_seat_service is not None:
                active_seat_service.stop()

    app = FastAPI(
        title="ClassCatalog API",
        version="0.1.0",
        description="Faceted SDSU class-search and public-catalog planning aid",
        lifespan=lifespan,
    )

    def _service_error_response(
        request: Request,
        *,
        status_code: int,
        error: Exception,
        detail: object,
    ) -> JSONResponse:
        request_id = secrets.token_hex(8)
        code, public_message = public_error_for_path(request.url.path)
        recent_error_store.record(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            code=code,
            public_message=public_message,
            exception_type=type(error).__name__,
            detail=detail,
        )
        headers = {"Cache-Control": "no-store", "X-Request-ID": request_id}
        return JSONResponse(
            status_code=status_code,
            content={
                "detail": public_message,
                "error": {
                    "code": code,
                    "message": public_message,
                    "request_id": request_id,
                }
            },
            headers=headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
            request: Request,
            exc: StarletteHTTPException,
    ):
        # API endpoints should always keep their JSON responses.
        if request.url.path.startswith("/api/"):
            if exc.status_code < 500:
                return await http_exception_handler(request, exc)

            LOGGER.warning(
                "API failure method=%s path=%s status=%s detail=%s",
                request.method,
                request.url.path,
                exc.status_code,
                exc.detail,
            )

            return _service_error_response(
                request,
                status_code=exc.status_code,
                error=exc,
                detail=exc.detail,
            )

        # Normal website 404.
        if exc.status_code == 404:
            return FileResponse(
                ERROR_PAGES_DIR / "404.html",
                status_code=404,
                media_type="text/html",
                )

        # Normal website server errors.
        if exc.status_code >= 500:
            LOGGER.warning(
                "Website failure method=%s path=%s status=%s detail=%s",
                request.method,
                request.url.path,
                exc.status_code,
                exc.detail,
            )

            return FileResponse(
                ERROR_PAGES_DIR / "500.html",
                status_code=exc.status_code,
                media_type="text/html",
                )

        return await http_exception_handler(request, exc)


    @app.exception_handler(Exception)
    async def handle_unexpected_exception(
            request: Request,
            exc: Exception,
    ):
        LOGGER.error(
            "Unhandled failure method=%s path=%s",
            request.method,
            request.url.path,
            exc_info=(type(exc), exc, exc.__traceback__),
        )

        if request.url.path.startswith("/api/"):
            return _service_error_response(
                request,
                status_code=500,
                error=exc,
                detail=exc,
            )

        return FileResponse(
            ERROR_PAGES_DIR / "500.html",
            status_code=500,
            media_type="text/html",
            )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        catalog_status = active_repository.catalog.status()
        return {
            "status": "ok",
            "classes": active_repository.total,
            "courses": active_repository.course_total,
            "physical_sections": active_repository.physical_section_total,
            "subjects": active_repository.subject_total,
            "terms": len(active_repository.options().terms),
            "catalog_loaded": catalog_status.loaded,
            "catalog_programs": catalog_status.programs,
            "catalog_requirements": catalog_status.requirements,
            "professor_ratings": active_repository.ratings_record_count,
            "seat_refresh": (
                active_seat_service.public_status()
                if active_seat_service is not None
                else {"enabled": False, "running": False}
            ),
        }

    @app.get("/api/seats/status")
    async def seat_refresh_status(response: Response) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        if active_seat_service is None:
            return {
                "enabled": False,
                "running": False,
                "refreshable_course_pages": 0,
                "cached_sections": 0,
                "last_success_at": None,
                "age_seconds": None,
                "browser_poll_seconds": 60,
            }
        return active_seat_service.public_status()

    @app.get("/api/ratings/status")
    async def ratings_status(response: Response) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        if active_repository.ratings_load_error:
            raise HTTPException(status_code=503, detail=active_repository.ratings_load_error)
        return {
            "available": active_repository.ratings_record_count > 0,
            "records": active_repository.ratings_record_count,
        }

    @app.get("/api/seats")
    async def current_seats(
        response: Response,
        schedule_number: Annotated[list[str] | None, Query()] = None,
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        requested = tuple(dict.fromkeys(
            str(value).strip() for value in (schedule_number or ()) if str(value).strip()
        ))
        if len(requested) > 250:
            raise HTTPException(status_code=400, detail="At most 250 class numbers can be requested at once.")
        if active_seat_service is not None:
            active_seat_service.register_interest(requested)
            records = active_seat_service.seat_records(requested)
        else:
            records = {
                f"{section.term}::{schedule}": {
                    "schedule_number": schedule,
                    "term": section.term,
                    "seat_status": section.seat_status.value,
                    "seats_available": section.seats_available,
                    "seat_capacity": section.seat_capacity,
                    "seats_enrolled": section.seats_enrolled,
                    "updated_at": None,
                }
                for (_term, schedule), section in active_repository.seat_snapshot(requested).items()
            }
        return {"records": records}

    @app.post("/api/seats/interest")
    async def seat_interest(payload: SeatInterestRequest) -> dict[str, int]:
        requested = tuple(dict.fromkeys(
            str(value).strip() for value in payload.schedule_numbers if str(value).strip()
        ))
        if len(requested) > 250:
            raise HTTPException(status_code=400, detail="At most 250 class numbers can be prioritized at once.")
        priority_sources = (
            active_seat_service.register_interest(requested)
            if active_seat_service is not None
            else 0
        )
        return {"priority_sources": priority_sources}

    @app.get("/api/admin/session")
    async def admin_session(request: Request, response: Response) -> dict[str, bool]:
        response.headers["Cache-Control"] = "no-store"
        return {
            "enabled": admin_enabled,
            "authenticated": _is_admin_authenticated(request),
        }

    @app.post("/api/admin/login")
    async def admin_login(
        payload: AdminLoginRequest,
        request: Request,
        response: Response,
    ) -> dict[str, bool]:
        response.headers["Cache-Control"] = "no-store"
        if not admin_enabled or configured_admin_password is None:
            raise HTTPException(status_code=404, detail="Admin access is not configured.")

        now = time_module.time()
        client_key = _client_key(request)
        attempts = [
            attempted_at
            for attempted_at in failed_admin_logins.get(client_key, [])
            if now - attempted_at < ADMIN_LOGIN_WINDOW_SECONDS
        ]
        failed_admin_logins[client_key] = attempts
        if len(attempts) >= ADMIN_LOGIN_MAX_FAILURES:
            raise HTTPException(
                status_code=429,
                detail="Too many failed admin login attempts. Try again later.",
                headers={"Retry-After": str(ADMIN_LOGIN_WINDOW_SECONDS)},
            )

        if not hmac.compare_digest(payload.password, configured_admin_password):
            attempts.append(now)
            failed_admin_logins[client_key] = attempts
            raise HTTPException(status_code=401, detail="Invalid admin password.")

        failed_admin_logins.pop(client_key, None)
        token = secrets.token_urlsafe(32)
        admin_sessions[token] = now + ADMIN_SESSION_MAX_AGE_SECONDS
        response.set_cookie(
            ADMIN_SESSION_COOKIE,
            token,
            max_age=ADMIN_SESSION_MAX_AGE_SECONDS,
            httponly=True,
            secure=_secure_cookie(request),
            samesite="strict",
            path="/",
        )
        return {"authenticated": True}

    @app.post("/api/admin/logout")
    async def admin_logout(request: Request, response: Response) -> dict[str, bool]:
        response.headers["Cache-Control"] = "no-store"
        token = request.cookies.get(ADMIN_SESSION_COOKIE)
        if token:
            admin_sessions.pop(token, None)
        response.delete_cookie(ADMIN_SESSION_COOKIE, path="/", samesite="strict")
        return {"authenticated": False}

    @app.get("/api/admin/health")
    async def admin_health(request: Request, response: Response) -> dict[str, object]:
        _require_admin(request)
        response.headers["Cache-Control"] = "no-store"
        manual_overrides, manual_ratings_error = _manual_rating_state()
        return _admin_health_snapshot(
            active_repository,
            data_path=active_data_path,
            catalog_path=active_catalog_path,
            ratings_path=active_ratings_path,
            seat_refresh_service=active_seat_service,
            recent_errors=recent_error_store.snapshot(),
            manual_rating_overrides=manual_overrides,
            manual_ratings_enabled=ratings_editor is not None,
            manual_ratings_error=manual_ratings_error,
        )

    @app.post("/api/admin/professors/overrides")
    async def admin_add_professor_override(
        payload: AdminProfessorOverrideRequest,
        request: Request,
        response: Response,
    ) -> dict[str, object]:
        _require_admin(request)
        response.headers["Cache-Control"] = "no-store"
        if ratings_editor is None or active_ratings_path is None:
            raise HTTPException(
                status_code=409,
                detail="A professor ratings cache path must be configured before adding manual matches.",
            )

        normalized_name = normalize_person_name(payload.instructor_name)
        targets = {
            str(item["normalized_name"]): item
            for item in _instructor_inventory(active_repository)
        }
        target = targets.get(normalized_name)
        if target is None:
            raise HTTPException(
                status_code=404,
                detail="Select an instructor from the active class schedule.",
            )

        try:
            manual_names = {
                normalize_person_name(str(item.get("name") or ""))
                for item in ratings_editor.manual_overrides()
            }
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=409,
                detail="The professor ratings cache could not be read safely. Fix it before adding a match.",
            ) from exc
        if bool(target["matched"]) and normalized_name not in manual_names:
            raise HTTPException(
                status_code=409,
                detail="This instructor already has an automatic RateMyProfessors match.",
            )

        try:
            profile_id = parse_rmp_profile_id(payload.rmp_profile)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        candidate = await run_in_threadpool(active_rmp_client.lookup_legacy_id, profile_id)
        if candidate is None or candidate.legacy_id != profile_id:
            raise HTTPException(
                status_code=404,
                detail="That RateMyProfessors profile could not be found.",
            )

        course_codes = target["course_codes"]
        assert isinstance(course_codes, list)
        record = ratings_editor.upsert(
            instructor_name=str(target["name"]),
            course_codes=course_codes,
            candidate=candidate,
        )
        changed_sections = active_repository.reload_ratings(active_ratings_path)
        return {
            "saved": True,
            "override": record,
            "updated_section_listings": changed_sections,
        }

    @app.delete("/api/admin/professors/overrides")
    async def admin_delete_professor_override(
        request: Request,
        response: Response,
        instructor_name: Annotated[str, Query(min_length=1, max_length=200)],
    ) -> dict[str, object]:
        _require_admin(request)
        response.headers["Cache-Control"] = "no-store"
        if ratings_editor is None or active_ratings_path is None:
            raise HTTPException(status_code=404, detail="Manual professor matching is not configured.")

        removed = ratings_editor.delete(instructor_name)
        if removed is None:
            raise HTTPException(status_code=404, detail="Manual professor match not found.")
        changed_sections = active_repository.reload_ratings(active_ratings_path)
        return {
            "deleted": True,
            "instructor_name": removed.get("name"),
            "updated_section_listings": changed_sections,
        }

    @app.post("/api/admin/seats/refresh")
    async def admin_refresh_seats(request: Request, response: Response) -> dict[str, object]:
        _require_admin(request)
        response.headers["Cache-Control"] = "no-store"
        if active_seat_service is None or not active_seat_service.enabled:
            raise HTTPException(status_code=503, detail="Seat refreshing is not available for this dataset.")
        queued = active_seat_service.request_full_refresh()
        return {"accepted": True, "queued_course_pages": queued}

    @app.post("/api/admin/seats/refresh/course")
    async def admin_refresh_course_seats(
        payload: AdminSeatRefreshRequest,
        request: Request,
        response: Response,
    ) -> dict[str, object]:
        _require_admin(request)
        response.headers["Cache-Control"] = "no-store"
        if active_seat_service is None or not active_seat_service.enabled:
            raise HTTPException(status_code=503, detail="Seat refreshing is not available for this dataset.")

        course_code = " ".join(payload.course_code.strip().upper().split())
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 &-")
        if not course_code or len(course_code) > 32 or any(char not in allowed for char in course_code):
            raise HTTPException(status_code=400, detail="Select a valid course code.")

        queued = active_seat_service.request_course_refresh(course_code)
        if queued == 0:
            raise HTTPException(
                status_code=404,
                detail=f"{course_code} is not in the active schedule or has no refreshable seat source.",
            )
        return {
            "accepted": True,
            "course_code": course_code,
            "queued_course_pages": queued,
        }

    @app.get("/api/options", response_model=SearchOptions)
    async def options() -> SearchOptions:
        return active_repository.options()

    @app.get("/api/catalog/status", response_model=CatalogStatus)
    async def catalog_status() -> CatalogStatus:
        _require_catalog_data()
        return active_repository.catalog.status()

    @app.get("/api/catalog/programs", response_model=CatalogProgramListResponse)
    async def catalog_programs(
        catalog_year: str | None = None,
        q: str | None = None,
        degree_type: str | None = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> CatalogProgramListResponse:
        _require_catalog_data()
        return active_repository.catalog.list_programs(
            catalog_year=catalog_year,
            query=q,
            degree_type=degree_type,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/catalog/program/detail", response_model=CatalogProgramDetail)
    async def catalog_program_detail(
        program: Annotated[str, Query(min_length=1)],
        catalog_year: Annotated[str, Query(min_length=1)],
    ) -> CatalogProgramDetail:
        _require_catalog_data()
        detail = active_repository.catalog.program_detail(program, catalog_year)
        if detail is None:
            raise HTTPException(
                status_code=404,
                detail=f"No catalog mapping found for {program!r} in {catalog_year!r}.",
            )
        return detail

    @app.get("/api/catalog/requirements", response_model=CatalogRequirementListResponse)
    async def catalog_requirements(
        catalog_year: str | None = None,
    ) -> CatalogRequirementListResponse:
        _require_catalog_data()
        return active_repository.catalog.list_requirements(catalog_year=catalog_year)

    @app.get("/api/catalog/requirement", response_model=CatalogRequirementDetail)
    async def catalog_requirement(
        code: Annotated[str, Query(min_length=1)],
        catalog_year: Annotated[str, Query(min_length=1)],
    ) -> CatalogRequirementDetail:
        _require_catalog_data()
        requirement = active_repository.catalog.get_requirement(code, catalog_year)
        if requirement is None:
            raise HTTPException(
                status_code=404,
                detail=f"No catalog requirement found for {code!r} in {catalog_year!r}.",
            )
        return requirement

    @app.get("/api/catalog/program", response_model=CatalogProgramSummary)
    async def catalog_program(
        program: Annotated[str, Query(min_length=1)],
        catalog_year: Annotated[str, Query(min_length=1)],
    ) -> CatalogProgramSummary:
        _require_catalog_data()
        summary = active_repository.catalog.program_summary(
            program,
            catalog_year,
            scheduled_course_codes=active_repository.scheduled_course_codes,
        )
        if summary is None:
            raise HTTPException(
                status_code=404,
                detail=f"No catalog mapping found for {program!r} in {catalog_year!r}.",
            )
        return summary

    @app.get("/api/profile/summary", response_model=StudentProfileSummary)
    async def profile_summary(
        program: Annotated[str, Query(min_length=1)],
        catalog_year: Annotated[str, Query(min_length=1)],
        completed_course: Annotated[list[str] | None, Query()] = None,
    ) -> StudentProfileSummary:
        _require_catalog_data()
        summary = active_repository.catalog.student_profile_summary(
            program,
            catalog_year,
            scheduled_course_codes=active_repository.scheduled_course_codes,
            completed_courses=completed_course or (),
        )
        if summary is None:
            raise HTTPException(
                status_code=404,
                detail=f"No catalog mapping found for {program!r} in {catalog_year!r}.",
            )
        return summary

    @app.get("/api/classes", response_model=SearchResponse)
    async def classes(
        term: Annotated[list[str] | None, Query()] = None,
        campus: Annotated[list[str] | None, Query()] = None,
        q: str | None = None,
        requirement: Annotated[list[str] | None, Query()] = None,
        units_min: Annotated[float | None, Query(ge=0)] = None,
        units_max: Annotated[float | None, Query(ge=0)] = None,
        grading: Annotated[list[GradingType] | None, Query()] = None,
        program: str | None = None,
        catalog_year: str | None = None,
        classification: Annotated[list[ProgramClassification] | None, Query()] = None,
        major_only: bool = True,
        completed_course: Annotated[list[str] | None, Query()] = None,
        day: Annotated[list[Weekday] | None, Query()] = None,
        exclude_day: Annotated[list[Weekday] | None, Query()] = None,
        time_from: time | None = None,
        time_to: time | None = None,
        instruction_mode: Annotated[list[InstructionMode] | None, Query()] = None,
        seat_status: Annotated[list[SeatStatus] | None, Query()] = None,
        rating_min: Annotated[float | None, Query(ge=0, le=5)] = None,
        difficulty_max: Annotated[float | None, Query(ge=0, le=5)] = None,
        would_take_again_min: Annotated[float | None, Query(ge=0, le=100)] = None,
        reviews_min: Annotated[int | None, Query(ge=0)] = None,
        attendance_required: bool | None = None,
        textbook_required: bool | None = None,
        sort_by: SortBy = SortBy.COURSE_A_Z,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=50)] = 50,
    ) -> SearchResponse:
        filters = SearchFilters(
            terms=tuple(term or ()),
            campuses=tuple(campus or ()),
            query=q,
            requirements=tuple(requirement or ()),
            units_min=units_min,
            units_max=units_max,
            gradings=tuple(grading or ()),
            program=program,
            catalog_year=catalog_year,
            classifications=tuple(classification or ()),
            major_only=major_only,
            completed_courses=tuple(completed_course or ()),
            days=tuple(day or ()),
            excluded_days=tuple(exclude_day or ()),
            time_from=time_from,
            time_to=time_to,
            instruction_modes=tuple(instruction_mode or ()),
            seat_statuses=tuple(seat_status or ()),
            rating_min=rating_min,
            difficulty_max=difficulty_max,
            would_take_again_min=would_take_again_min,
            reviews_min=reviews_min,
            attendance_required=attendance_required,
            textbook_required=textbook_required,
            sort_by=sort_by,
            page=page,
            page_size=page_size,
        )
        result = active_repository.search(filters)
        if active_seat_service is not None:
            schedules = []
            for item in result.items:
                schedules.append(item.schedule_number)
                schedules.extend(component.schedule_number for component in item.linked_components)
            active_seat_service.register_interest(tuple(schedules))
        return result

    return app


app = create_app()


def run() -> None:
    uvicorn.run("classcatalog.main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    run()
