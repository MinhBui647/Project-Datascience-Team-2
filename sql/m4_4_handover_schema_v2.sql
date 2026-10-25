-- M4.4.4.3 | One-time handover schema installation for Raw Storage V2.
-- No INSERT/UPDATE/DELETE/ALTER/DROP/TRUNCATE; no raw.* table changes.
-- Run using scripts/raw_access/install_handover_schema.py, NOT directly.
DO $guard$
BEGIN
    IF current_database() <> 'epl_raw_storage_v2' THEN
        RAISE EXCEPTION 'V2 DATABASE GUARD: connected to %', current_database();
    END IF;
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'handover') THEN
        RAISE EXCEPTION 'SCHEMA ALREADY EXISTS: handover. Review existing schema; never silently overwrite.';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM raw.snapshot_runs
        WHERE snapshot_id = 4 AND status = 'completed'
          AND manifest_sha256 IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'BASELINE GATE: completed snapshot #4 + manifest required';
    END IF;
    IF (SELECT COUNT(*) FROM raw.file_observations
        WHERE snapshot_id = 4 AND presence_status = 'linked' AND file_id IS NOT NULL) <> 276 THEN
        RAISE EXCEPTION 'BASELINE GATE: expected 276 linked file versions';
    END IF;
END
$guard$;

CREATE SCHEMA handover;

CREATE TABLE handover.delivery_checkpoints (
    checkpoint_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    consumer_code TEXT NOT NULL,
    file_id BIGINT NOT NULL REFERENCES raw.raw_files(file_id) ON DELETE RESTRICT,
    first_snapshot_id BIGINT NOT NULL REFERENCES raw.snapshot_runs(snapshot_id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    export_manifest_sha256 TEXT,
    receipt_evidence JSONB,
    processing_evidence JSONB,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_handover_consumer_file UNIQUE (consumer_code, file_id),
    CONSTRAINT ck_handover_consumer CHECK (btrim(consumer_code) <> ''),
    CONSTRAINT ck_handover_state CHECK (status IN ('PENDING','EXPORTED','RECEIVED','PROCESSED','FAILED')),
    CONSTRAINT ck_handover_hash CHECK (
        export_manifest_sha256 IS NULL OR export_manifest_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_handover_export_evidence CHECK (
        status NOT IN ('EXPORTED','RECEIVED','PROCESSED') OR export_manifest_sha256 IS NOT NULL
    ),
    CONSTRAINT ck_handover_receipt_evidence CHECK (
        status NOT IN ('RECEIVED','PROCESSED') OR receipt_evidence IS NOT NULL
    ),
    CONSTRAINT ck_handover_processed_evidence CHECK (
        status <> 'PROCESSED' OR processing_evidence IS NOT NULL
    )
);

CREATE TABLE handover.delivery_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    checkpoint_id BIGINT NOT NULL REFERENCES handover.delivery_checkpoints(checkpoint_id) ON DELETE RESTRICT,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor TEXT NOT NULL,
    evidence JSONB,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_handover_event_actor CHECK (btrim(actor) <> ''),
    CONSTRAINT ck_handover_event_to CHECK (to_status IN ('PENDING','EXPORTED','RECEIVED','PROCESSED','FAILED')),
    CONSTRAINT ck_handover_event_from CHECK (
        from_status IS NULL OR from_status IN ('PENDING','EXPORTED','RECEIVED','PROCESSED','FAILED')
    )
);

CREATE INDEX idx_handover_checkpoints_consumer_status
    ON handover.delivery_checkpoints (consumer_code, status);
CREATE INDEX idx_handover_events_checkpoint
    ON handover.delivery_events (checkpoint_id, event_id);

-- IMPORTANT: SQL constraints alone cannot enforce permitted transitions,
-- immutable events, manifest/full-file export evidence, or Cleaning actor authorization.
-- Enforce these atomically in the later checkpoint write service (separate checkpoint).
