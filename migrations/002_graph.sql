-- Idempotent. Applied on every startup.

CREATE TABLE IF NOT EXISTS entities (
    id          BIGSERIAL PRIMARY KEY,
    type        TEXT NOT NULL,
    name        TEXT NOT NULL,
    name_key    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (type, name_key)
);

CREATE TABLE IF NOT EXISTS entity_sources (
    entity_id   BIGINT NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    source_id   TEXT NOT NULL,
    fact_id     BIGINT REFERENCES facts (id) ON DELETE SET NULL,
    PRIMARY KEY (entity_id, source_id)
);

CREATE INDEX IF NOT EXISTS entity_sources_source_id ON entity_sources (source_id);

CREATE TABLE IF NOT EXISTS links (
    id          BIGSERIAL PRIMARY KEY,
    from_id     BIGINT NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    type        TEXT NOT NULL,
    to_id       BIGINT NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    source_id   TEXT NOT NULL,
    fact_id     BIGINT REFERENCES facts (id) ON DELETE SET NULL,
    UNIQUE (from_id, type, to_id, source_id)
);

CREATE INDEX IF NOT EXISTS links_from_id ON links (from_id);

CREATE INDEX IF NOT EXISTS links_to_id ON links (to_id);
