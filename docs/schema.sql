-- Production-oriented PostgreSQL starting point. The runnable demo uses JSON in memory.

create table terms (
    id bigserial primary key,
    source_key text not null unique,
    name text not null,
    starts_on date,
    ends_on date,
    is_published boolean not null default true,
    source_updated_at timestamptz,
    fetched_at timestamptz not null default now()
);

create table courses (
    id bigserial primary key,
    subject text not null,
    catalog_number text not null,
    course_code text not null,
    title text not null,
    description text,
    units_min numeric(4,1),
    units_max numeric(4,1),
    grading_type text,
    prerequisite_text text,
    prerequisite_rule jsonb not null default '{}'::jsonb,
    catalog_year text not null,
    source_url text,
    source_updated_at timestamptz,
    fetched_at timestamptz not null default now(),
    unique (catalog_year, course_code)
);

create table sections (
    id bigserial primary key,
    source_key text not null unique,
    term_id bigint not null references terms(id),
    course_id bigint not null references courses(id),
    section_number text not null,
    schedule_number text not null,
    component text,
    campus text,
    instruction_mode text not null,
    seat_status text not null,
    seats_available integer,
    seats_enrolled integer,
    seat_capacity integer,
    waitlist_available integer,
    notes text,
    source_url text,
    source_updated_at timestamptz,
    fetched_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now()
);

create index sections_term_status_idx on sections(term_id, seat_status);
create index sections_course_idx on sections(course_id);

create table meetings (
    id bigserial primary key,
    section_id bigint not null references sections(id) on delete cascade,
    days bit(7),
    starts_at time,
    ends_at time,
    starts_on date,
    ends_on date,
    location text,
    meeting_mode text
);

create index meetings_section_idx on meetings(section_id);

create table instructors (
    id bigserial primary key,
    normalized_name text not null,
    display_name text not null,
    department text,
    unique (normalized_name, department)
);

create table section_instructors (
    section_id bigint not null references sections(id) on delete cascade,
    instructor_id bigint not null references instructors(id),
    role text,
    primary key (section_id, instructor_id)
);

create table course_metrics (
    id bigserial primary key,
    course_id bigint not null references courses(id) on delete cascade,
    provider text not null,
    class_difficulty numeric(3,2),
    num_reviews integer not null default 0,
    source_updated_at timestamptz,
    fetched_at timestamptz not null default now(),
    unique (course_id, provider)
);

create table professor_metrics (
    id bigserial primary key,
    instructor_id bigint not null references instructors(id),
    provider text not null,
    external_id text,
    course_code text,
    rating numeric(3,2),
    difficulty numeric(3,2),
    would_take_again_percent numeric(5,2),
    num_reviews integer not null default 0,
    attendance_required boolean,
    textbook_required boolean,
    profile_url text,
    match_confidence numeric(4,3),
    source_updated_at timestamptz,
    fetched_at timestamptz not null default now(),
    unique (provider, external_id, course_code)
);

create table requirement_definitions (
    id bigserial primary key,
    catalog_year text not null,
    code text not null,
    name text not null,
    unique (catalog_year, code)
);

create table course_requirement_mappings (
    course_id bigint not null references courses(id) on delete cascade,
    requirement_id bigint not null references requirement_definitions(id) on delete cascade,
    primary key (course_id, requirement_id)
);

create table program_course_classifications (
    id bigserial primary key,
    program text not null,
    catalog_year text not null,
    course_id bigint not null references courses(id) on delete cascade,
    classification text not null check (classification in ('major_prep', 'major_course', 'elective')),
    source_url text,
    unique (program, catalog_year, course_id, classification)
);

create index program_classification_lookup_idx
    on program_course_classifications(program, catalog_year, classification);

create table sync_runs (
    id bigserial primary key,
    source_name text not null,
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    status text not null,
    requested_count integer not null default 0,
    completed_count integer not null default 0,
    published boolean not null default false,
    metadata jsonb not null default '{}'::jsonb
);

create table sync_errors (
    id bigserial primary key,
    sync_run_id bigint not null references sync_runs(id) on delete cascade,
    term_name text,
    subject text,
    error_type text,
    message text not null,
    occurred_at timestamptz not null default now()
);
