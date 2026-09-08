"""Baseline schema marker for existing TENGSL databases.

The application keeps its compatibility create_all() path for already deployed
installations. New schema changes must be introduced as normal Alembic revisions.
"""
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    pass

def downgrade():
    pass
