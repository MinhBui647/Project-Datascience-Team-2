-- EPL Raw Storage V2 | M2.2 | PostgreSQL 14+
-- One-time schema creation ONLY in database epl_raw_storage_v2.
-- Intentionally not an ALTER/migration of the V1 database.
-- Run with scripts/tools/apply_raw_schema_v2.py for preflight + transaction.
-- Seven tables; no INSERT, UPDATE, DELETE, DROP or TRUNCATE statements.

-- Protection is implemented again inside SQL so even manual execution
-- in pgAdmin/psql cannot accidentally create these objects in V1.
DO $guard$
BEGIN
    IF current_database() <> 'epl_raw_storage_v2' THEN
        RAISE EXCEPTION 'V2 SAFETY GUARD: expected epl_raw_storage_v2, connected to %', current_database();
    END IF;
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'raw') THEN
        RAISE EXCEPTION 'V2 SAFETY GUARD: schema raw already exists; refusing one-time bootstrap. Inspect before migration.';
    END IF;
END
$guard$;

CREATE SCHEMA raw;

-- 1) Source/dataset folder classifications. A folder label is not
-- necessarily the verified upstream data provider.
CREATE TABLE raw.raw_sources (
    source_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_code        VARCHAR(80) NOT NULL UNIQUE,
    source_name        VARCHAR(200) NOT NULL,
    source_url         TEXT,
    upstream_provider  TEXT,
    description        TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_sources_code_not_blank CHECK (btrim(source_code) <> ''),
    CONSTRAINT ck_sources_name_not_blank CHECK (btrim(source_name) <> '')
);

-- 2) Hugging Face repository revision observed at a given time.
CREATE TABLE raw.snapshot_runs (
    snapshot_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    repo_id              TEXT NOT NULL,
    hf_revision          VARCHAR(40) NOT NULL,
    observed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    manifest_archive_path TEXT,
    manifest_sha256      CHAR(64),
    manifest_file_count  INTEGER,
    raw_scope_file_count INTEGER,
    status               VARCHAR(20) NOT NULL DEFAULT 'discovered',
    CONSTRAINT uq_snapshot_repo_revision UNIQUE (repo_id, hf_revision),
    -- Enables FK enforcing snapshot_id + repository identity in observations.
    CONSTRAINT uq_snapshot_id_repo UNIQUE (snapshot_id, repo_id),
    CONSTRAINT ck_snapshot_repo CHECK (btrim(repo_id) <> ''),
    CONSTRAINT ck_snapshot_revision CHECK (hf_revision ~ '^[0-9a-f]{40}$'),
    CONSTRAINT ck_snapshot_manifest_hash CHECK (
        manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_snapshot_manifest_pair CHECK (
        (manifest_sha256 IS NULL) = (manifest_archive_path IS NULL)
    ),
    CONSTRAINT ck_snapshot_manifest_count CHECK (
        manifest_file_count IS NULL OR manifest_file_count >= 0
    ),
    CONSTRAINT ck_snapshot_raw_count CHECK (
        raw_scope_file_count IS NULL OR raw_scope_file_count >= 0
    ),
    CONSTRAINT ck_snapshot_counts_order CHECK (
        manifest_file_count IS NULL OR raw_scope_file_count IS NULL
        OR raw_scope_file_count <= manifest_file_count
    ),
    CONSTRAINT ck_snapshot_status CHECK (
        status IN ('discovered','in_progress','completed','failed')
    ),
    CONSTRAINT ck_snapshot_completed_manifest CHECK (
        status <> 'completed' OR manifest_sha256 IS NOT NULL
    )
);

-- 3) One row per (repository, POSIX relative path, downloaded byte SHA-256).
-- Register only after original bytes are verified in an immutable V2 archive.
CREATE TABLE raw.raw_files (
    file_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    repo_id             TEXT NOT NULL,
    relative_path       TEXT NOT NULL,
    source_id           BIGINT REFERENCES raw.raw_sources(source_id) ON DELETE RESTRICT,
    file_format         VARCHAR(20) NOT NULL,
    content_sha256      CHAR(64) NOT NULL,
    file_size_bytes     BIGINT NOT NULL,
    archive_path        TEXT NOT NULL,
    archive_state       VARCHAR(20) NOT NULL DEFAULT 'verified',
    parse_state         VARCHAR(20) NOT NULL DEFAULT 'pending',
    record_count        BIGINT,
    header_columns      JSONB,
    league_scope        VARCHAR(10) NOT NULL DEFAULT 'unknown',
    league_code         TEXT,
    season_scope        VARCHAR(10) NOT NULL DEFAULT 'unknown',
    season              VARCHAR(9),
    metadata_evidence   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_file_version UNIQUE (repo_id, relative_path, content_sha256),
    -- Enables FK assuring observation points to same repo AND path.
    CONSTRAINT uq_file_id_repo_path UNIQUE (file_id, repo_id, relative_path),
    CONSTRAINT ck_file_repo CHECK (btrim(repo_id) <> ''),
    CONSTRAINT ck_file_path CHECK (
        btrim(relative_path) <> '' AND left(relative_path, 1) <> '/'
        AND position(chr(92) IN relative_path) = 0
        AND relative_path !~ '(^|/)\.\.(/|$)'
        AND relative_path !~ '(^|/)\.(/|$)'
        AND relative_path !~ '//'
    ),
    CONSTRAINT ck_file_archive_path CHECK (btrim(archive_path) <> ''),
    CONSTRAINT ck_file_format CHECK (file_format IN ('csv','csv.gz','json','tar.gz')),
    CONSTRAINT ck_file_sha CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_file_size CHECK (file_size_bytes >= 0),
    CONSTRAINT ck_file_archive_state CHECK (archive_state IN ('verified','quarantined')),
    CONSTRAINT ck_file_parse_state CHECK (parse_state IN ('pending','success','failed','partial')),
    CONSTRAINT ck_file_count CHECK (record_count IS NULL OR record_count >= 0),
    CONSTRAINT ck_file_success_count CHECK (parse_state <> 'success' OR record_count IS NOT NULL),
    CONSTRAINT ck_file_headers CHECK (
        header_columns IS NULL OR jsonb_typeof(header_columns) = 'array'
    ),
    CONSTRAINT ck_file_league_scope CHECK (
        (league_scope = 'single' AND league_code IS NOT NULL AND btrim(league_code) <> '')
        OR (league_scope IN ('mixed','unknown') AND league_code IS NULL)
    ),
    CONSTRAINT ck_file_season_scope CHECK (
        (season_scope = 'single' AND season IS NOT NULL)
        OR (season_scope IN ('mixed','unknown') AND season IS NULL)
    ),
    CONSTRAINT ck_file_season_format CHECK (
        season IS NULL OR season ~ '^[0-9]{4}-[0-9]{4}$'
    ),
    CONSTRAINT ck_file_evidence CHECK (jsonb_typeof(metadata_evidence) = 'object')
);

-- 4) One path in a snapshot: may be merely discovered, linked to an
-- archived file version, or explicitly absent (only after full manifest).
-- repo_id is deliberately duplicated to enforce consistency using FKs.
CREATE TABLE raw.file_observations (
    observation_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    snapshot_id        BIGINT NOT NULL,
    repo_id            TEXT NOT NULL,
    relative_path      TEXT NOT NULL,
    file_id            BIGINT,
    presence_status    VARCHAR(20) NOT NULL DEFAULT 'discovered',
    manifest_size_bytes BIGINT,
    git_blob_id        TEXT,
    lfs_sha256         CHAR(64),
    observed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_observation_path UNIQUE (snapshot_id, relative_path),
    CONSTRAINT fk_observation_snapshot_repo FOREIGN KEY (snapshot_id, repo_id)
        REFERENCES raw.snapshot_runs(snapshot_id, repo_id) ON DELETE RESTRICT,
    CONSTRAINT fk_observation_file_repo_path FOREIGN KEY (file_id, repo_id, relative_path)
        REFERENCES raw.raw_files(file_id, repo_id, relative_path) ON DELETE RESTRICT,
    CONSTRAINT ck_observation_status CHECK (presence_status IN ('discovered','linked','absent')),
    CONSTRAINT ck_observation_linked CHECK (
        (presence_status = 'linked' AND file_id IS NOT NULL)
        OR (presence_status IN ('discovered','absent') AND file_id IS NULL)
    ),
    CONSTRAINT ck_observation_manifest_size CHECK (
        manifest_size_bytes IS NULL OR manifest_size_bytes >= 0
    ),
    CONSTRAINT ck_observation_lfs_sha CHECK (
        lfs_sha256 IS NULL OR lfs_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_observation_absent CHECK (
        presence_status <> 'absent' OR
        (manifest_size_bytes IS NULL AND git_blob_id IS NULL AND lfs_sha256 IS NULL)
    ),
    CONSTRAINT ck_observation_path CHECK (
        btrim(relative_path) <> '' AND left(relative_path, 1) <> '/'
        AND position(chr(92) IN relative_path) = 0
        AND relative_path !~ '(^|/)\.\.(/|$)'
        AND relative_path !~ '(^|/)\.(/|$)'
        AND relative_path !~ '//'
    )
);

-- 5) Ordered members inside a TAR.GZ file; duplicate member paths
-- are rejected, not silently overwritten.
CREATE TABLE raw.archive_members (
    member_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id             BIGINT NOT NULL REFERENCES raw.raw_files(file_id) ON DELETE RESTRICT,
    member_index        INTEGER NOT NULL,
    member_path         TEXT NOT NULL,
    member_size_bytes   BIGINT NOT NULL,
    member_sha256       CHAR(64),
    member_format       VARCHAR(20) NOT NULL DEFAULT 'unknown',
    parse_state         VARCHAR(20) NOT NULL DEFAULT 'pending',
    error_message       TEXT,
    CONSTRAINT uq_member_position UNIQUE (file_id, member_index),
    CONSTRAINT uq_member_path UNIQUE (file_id, member_path),
    -- Enables record -> (member_id, file_id) consistency FK.
    CONSTRAINT uq_member_id_file UNIQUE (member_id, file_id),
    CONSTRAINT ck_member_index CHECK (member_index >= 0),
    CONSTRAINT ck_member_size CHECK (member_size_bytes >= 0),
    CONSTRAINT ck_member_sha CHECK (
        member_sha256 IS NULL OR member_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_member_format CHECK (member_format IN ('json','other','unknown')),
    CONSTRAINT ck_member_state CHECK (parse_state IN ('pending','success','failed','skipped')),
    CONSTRAINT ck_member_safe_path CHECK (
        btrim(member_path) <> '' AND left(member_path, 1) <> '/'
        AND position(chr(92) IN member_path) = 0
        AND member_path !~ '(^|/)\.\.(/|$)'
        AND member_path !~ '(^|/)\.(/|$)'
        AND member_path !~ '//'
    )
);

-- 6) Searchable parsed copy. No semantic cleaning; an object JSONB
-- per CSV row, JSON array element, top-level object or JSON TAR member.
-- Any scalar is wrapped in an object while archive bytes remain unchanged.
CREATE TABLE raw.raw_records (
    record_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id             BIGINT NOT NULL REFERENCES raw.raw_files(file_id) ON DELETE RESTRICT,
    member_id           BIGINT,
    record_locator      TEXT NOT NULL,
    record_kind         VARCHAR(20) NOT NULL,
    raw_data            JSONB NOT NULL,
    league_code         TEXT,
    season              VARCHAR(9),
    event_time          TIMESTAMPTZ,
    metadata_evidence   JSONB,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_record_locator UNIQUE (file_id, record_locator),
    CONSTRAINT fk_record_member_same_file FOREIGN KEY (member_id, file_id)
        REFERENCES raw.archive_members(member_id, file_id) ON DELETE RESTRICT,
    CONSTRAINT ck_record_locator CHECK (btrim(record_locator) <> ''),
    CONSTRAINT ck_record_kind CHECK (
        record_kind IN ('csv_row','json_item','json_object','json_scalar','tar_json')
    ),
    CONSTRAINT ck_record_member_kind CHECK (
        (record_kind = 'tar_json' AND member_id IS NOT NULL)
        OR (record_kind <> 'tar_json' AND member_id IS NULL)
    ),
    CONSTRAINT ck_record_object CHECK (jsonb_typeof(raw_data) = 'object'),
    CONSTRAINT ck_record_season CHECK (
        season IS NULL OR season ~ '^[0-9]{4}-[0-9]{4}$'
    ),
    CONSTRAINT ck_record_evidence CHECK (
        metadata_evidence IS NULL OR jsonb_typeof(metadata_evidence) = 'object'
    )
);

-- 7) Batch/file attempt observability. Ingestion application must
-- persist failure logs OUTSIDE the rolled-back payload transaction.
CREATE TABLE raw.ingestion_logs (
    log_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id            UUID NOT NULL,
    snapshot_id         BIGINT REFERENCES raw.snapshot_runs(snapshot_id) ON DELETE RESTRICT,
    file_id             BIGINT REFERENCES raw.raw_files(file_id) ON DELETE RESTRICT,
    relative_path       TEXT NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'started',
    rows_inserted       BIGINT NOT NULL DEFAULT 0,
    attempt_number      INTEGER NOT NULL DEFAULT 1,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    error_message       TEXT,
    CONSTRAINT ck_log_path CHECK (btrim(relative_path) <> ''),
    CONSTRAINT ck_log_status CHECK (status IN ('started','success','skipped','failed')),
    CONSTRAINT ck_log_attempt CHECK (attempt_number >= 1),
    CONSTRAINT ck_log_rows CHECK (rows_inserted >= 0),
    CONSTRAINT ck_log_terminal_time CHECK (
        (status = 'started' AND finished_at IS NULL)
        OR (status <> 'started' AND finished_at IS NOT NULL)
    ),
    CONSTRAINT ck_log_order CHECK (finished_at IS NULL OR finished_at >= started_at),
    CONSTRAINT ck_log_success_file CHECK (status <> 'success' OR file_id IS NOT NULL),
    CONSTRAINT ck_log_no_insert_on_skip_fail CHECK (
        status NOT IN ('skipped','failed') OR rows_inserted = 0
    )
);

-- Indexes for the access patterns to be tested at M2.4 / M5.
CREATE INDEX idx_raw_files_source ON raw.raw_files(source_id);
CREATE INDEX idx_raw_files_league_season ON raw.raw_files(league_code, season)
    WHERE league_code IS NOT NULL OR season IS NOT NULL;
CREATE INDEX idx_raw_files_parse_state ON raw.raw_files(parse_state);
CREATE INDEX idx_snapshot_repo_time ON raw.snapshot_runs(repo_id, observed_at DESC);
CREATE INDEX idx_observations_file ON raw.file_observations(file_id)
    WHERE file_id IS NOT NULL;
CREATE INDEX idx_raw_records_league_season_time
    ON raw.raw_records(league_code, season, event_time)
    WHERE league_code IS NOT NULL;
CREATE INDEX idx_logs_batch ON raw.ingestion_logs(batch_id, attempt_number);
CREATE INDEX idx_logs_status_time ON raw.ingestion_logs(status, started_at DESC);

COMMENT ON SCHEMA raw IS 'EPL project raw landing layer: immutable source provenance, versioned snapshots, parsed JSONB.';
COMMENT ON TABLE raw.raw_files IS 'Content-addressed logical file versions; archive bytes are authoritative, JSONB derived.';
COMMENT ON TABLE raw.file_observations IS 'Repository revision membership; never infer absence from download errors.';
COMMENT ON COLUMN raw.file_observations.repo_id IS 'Duplicated for composite FK enforcing same repo across snapshot and file.';
COMMENT ON TABLE raw.raw_records IS 'Source-grain JSONB records; no EPL-only filtering, no business entity flattening.';
COMMENT ON TABLE raw.archive_members IS 'Ordered TAR members; parser enforces archive safety and byte quotas.';
