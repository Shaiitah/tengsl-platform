"""Add persistent diagnostics test run journal."""
from alembic import op
import sqlalchemy as sa

revision = "0002_test_runs"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "test_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("overall_status", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("suites_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("failures_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("output", sa.Text(), nullable=False, server_default=""),
        sa.Column("requested_by", sa.String(length=128), nullable=False, server_default=""),
    )
    op.create_index("ix_test_runs_started_at", "test_runs", ["started_at"])
    op.create_index("ix_test_runs_status", "test_runs", ["status"])


def downgrade():
    op.drop_index("ix_test_runs_status", table_name="test_runs")
    op.drop_index("ix_test_runs_started_at", table_name="test_runs")
    op.drop_table("test_runs")
