"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-15 00:00:00.000000
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
            event_id       TEXT             PRIMARY KEY,
            timestamp      TIMESTAMPTZ      NOT NULL,
            model          TEXT             NOT NULL,
            provider       TEXT             NOT NULL,
            input_tokens   INTEGER          NOT NULL DEFAULT 0,
            output_tokens  INTEGER          NOT NULL DEFAULT 0,
            total_tokens   INTEGER          NOT NULL DEFAULT 0,
            cost           DOUBLE PRECISION NOT NULL DEFAULT 0,
            latency_ms     INTEGER          NOT NULL DEFAULT 0,
            labels         JSONB            NOT NULL DEFAULT '{}'::jsonb,
            prompt         TEXT,
            response       TEXT,
            metadata       JSONB            NOT NULL DEFAULT '{}'::jsonb,
            schema_version INTEGER          NOT NULL DEFAULT 1
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_events_timestamp "
        "ON llm_events (timestamp DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_events_model "
        "ON llm_events (model);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_events_labels "
        "ON llm_events USING GIN (labels);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_llm_events_labels;")
    op.execute("DROP INDEX IF EXISTS idx_llm_events_model;")
    op.execute("DROP INDEX IF EXISTS idx_llm_events_timestamp;")
    op.execute("DROP TABLE IF EXISTS llm_events;")
