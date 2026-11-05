-- Idempotent. Applied on every startup.

ALTER TABLE facts ADD COLUMN IF NOT EXISTS useful_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE facts ADD COLUMN IF NOT EXISTS shown_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE facts ADD COLUMN IF NOT EXISTS last_useful_at TIMESTAMPTZ;

ALTER TABLE facts ADD COLUMN IF NOT EXISTS decay_score REAL NOT NULL DEFAULT 1;

ALTER TABLE facts ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

ALTER TABLE facts DROP CONSTRAINT IF EXISTS facts_source_id_chunk_idx_key;

CREATE UNIQUE INDEX IF NOT EXISTS facts_live_source_chunk
    ON facts (source_id, chunk_idx)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS chunk_feedback (
    id         BIGSERIAL PRIMARY KEY,
    fact_id    BIGINT NOT NULL REFERENCES facts (id) ON DELETE CASCADE,
    verdict    TEXT NOT NULL CHECK (verdict IN ('useful', 'wrong', 'outdated')),
    note       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chunk_feedback_fact_id ON chunk_feedback (fact_id);
