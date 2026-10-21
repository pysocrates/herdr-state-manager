"""Add independent, reversible governance pause; preserve existing task lifecycle."""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tasks', sa.Column('governance_paused', sa.Boolean(), nullable=False, server_default=sa.false()))
    for name in ('governance_changed_at', 'governance_changed_by', 'governance_reason'):
        op.add_column('tasks', sa.Column(name, sa.String(), nullable=True))


def downgrade():
    raise RuntimeError('Forward-only migration; restore a verified backup instead')
