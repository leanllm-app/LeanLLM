"""add module 1-6 fields

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-27 00:00:00.000000
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE llm_events
        ADD COLUMN correlation_id TEXT,
        ADD COLUMN parent_request_id TEXT,
        ADD COLUMN parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
        ADD COLUMN tools JSONB,
        ADD COLUMN tool_calls JSONB,
        ADD COLUMN time_to_first_token_ms INTEGER,
        ADD COLUMN total_stream_time_ms INTEGER,
        ADD COLUMN error_kind TEXT,
        ADD COLUMN error_message TEXT,
        ADD COLUMN normalized_input JSONB,
        ADD COLUMN normalized_output JSONB;
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_events_correlation_id "
        "ON llm_events (correlation_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_events_parent_request_id "
        "ON llm_events (parent_request_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_events_error_kind "
        "ON llm_events (error_kind);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_llm_events_error_kind;")
    op.execute("DROP INDEX IF EXISTS idx_llm_events_parent_request_id;")
    op.execute("DROP INDEX IF EXISTS idx_llm_events_correlation_id;")
    op.execute(
        """
        ALTER TABLE llm_events
        DROP COLUMN IF EXISTS correlation_id,
        DROP COLUMN IF EXISTS parent_request_id,
        DROP COLUMN IF EXISTS parameters,
        DROP COLUMN IF EXISTS tools,
        DROP COLUMN IF EXISTS tool_calls,
        DROP COLUMN IF EXISTS time_to_first_token_ms,
        DROP COLUMN IF EXISTS total_stream_time_ms,
        DROP COLUMN IF EXISTS error_kind,
        DROP COLUMN IF EXISTS error_message,
        DROP COLUMN IF EXISTS normalized_input,
        DROP COLUMN IF EXISTS normalized_output;
        """
    )
