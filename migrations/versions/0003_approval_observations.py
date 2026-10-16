"""Separate native approval observations and durable event deduplication."""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('approval_observations',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('project_id', sa.String(), sa.ForeignKey('projects.id'), nullable=False),
        *[sa.Column(k, sa.String(), nullable=False) for k in
          ('reporter', 'correlation_key', 'correlation', 'surface', 'status', 'created_at')],
        *[sa.Column(k, sa.String()) for k in
          ('native_request_id', 'session_key_hash', 'command_hash', 'choice', 'requested_at', 'responded_at')],
        sa.UniqueConstraint('project_id', 'reporter', 'correlation_key'))
    op.create_index('ix_approval_observations_project_id', 'approval_observations', ['project_id'])
    op.create_table('approval_observation_receipts',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('digest', sa.String(), nullable=False),
        sa.Column('observation_id', sa.String(), sa.ForeignKey('approval_observations.id'), nullable=False))


def downgrade():
    op.drop_table('approval_observation_receipts')
    op.drop_table('approval_observations')
