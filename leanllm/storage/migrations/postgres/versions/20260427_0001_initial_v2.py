"""initial schema v2 (consolidated)

Revision ID: 0001
Revises:
Create Date: 2026-04-27 00:00:00.000000

This consolidates the old 0001 (initial) + 0002 (add module fields) into a single
DDL. Justified by the "schema bump v2 — no events shipped yet" architectural
decision: with no production data, there is no upgrade path to preserve.
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_events (
            event_id                TEXT             PRIMARY KEY,
            timestamp               TIMESTAMPTZ      NOT NULL,
            model                   TEXT             NOT NULL,
            provider                TEXT             NOT NULL,

            input_tokens            INTEGER          NOT NULL DEFAULT 0,
            output_tokens           INTEGER          NOT NULL DEFAULT 0,
            total_tokens            INTEGER          NOT NULL DEFAULT 0,
            cost                    DOUBLE PRECISION NOT NULL DEFAULT 0,
            latency_ms              INTEGER          NOT NULL DEFAULT 0,

            labels                  JSONB            NOT NULL DEFAULT '{}'::jsonb,
            prompt                  TEXT,
            response                TEXT,
            metadata                JSONB            NOT NULL DEFAULT '{}'::jsonb,
            schema_version          INTEGER          NOT NULL DEFAULT 2,

            correlation_id          TEXT,
            parent_request_id       TEXT,
            parameters              JSONB            NOT NULL DEFAULT '{}'::jsonb,
            tools                   JSONB,
            tool_calls              JSONB,
            time_to_first_token_ms  INTEGER,
            total_stream_time_ms    INTEGER,
            error_kind              TEXT,
            error_message           TEXT,
            normalized_input        JSONB,
            normalized_output       JSONB
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_events_timestamp ON llm_events (timestamp DESC);"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_llm_events_model ON llm_events (model);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_events_correlation_id ON llm_events (correlation_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_events_parent_request_id ON llm_events (parent_request_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_events_error_kind ON llm_events (error_kind);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_events_labels ON llm_events USING GIN (labels);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_llm_events_labels;")
    op.execute("DROP INDEX IF EXISTS idx_llm_events_error_kind;")
    op.execute("DROP INDEX IF EXISTS idx_llm_events_parent_request_id;")
    op.execute("DROP INDEX IF EXISTS idx_llm_events_correlation_id;")
    op.execute("DROP INDEX IF EXISTS idx_llm_events_model;")
    op.execute("DROP INDEX IF EXISTS idx_llm_events_timestamp;")
    op.execute("DROP TABLE IF EXISTS llm_events;")
