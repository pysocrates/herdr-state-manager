"""Runtime incident lifecycle and operation-specific approvals."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('incidents', sa.Column('id', sa.String(), primary_key=True), sa.Column('project_id', sa.String(), sa.ForeignKey('projects.id'), nullable=False), sa.Column('incident_type', sa.String(), nullable=False), sa.Column('resource_id', sa.String(), nullable=False), sa.Column('first_seen_at', sa.String(), nullable=False), sa.Column('last_seen_at', sa.String(), nullable=False), sa.Column('status', sa.String(), nullable=False), sa.Column('resolved_at', sa.String()), sa.UniqueConstraint('project_id', 'incident_type', 'resource_id'))
    op.create_table('adapter_health', sa.Column('id', sa.String(), primary_key=True), sa.Column('project_id', sa.String(), sa.ForeignKey('projects.id'), nullable=False), sa.Column('provider', sa.String(), nullable=False), sa.Column('status', sa.String(), nullable=False), sa.Column('observed_at', sa.String(), nullable=False), sa.UniqueConstraint('project_id', 'provider'))
    op.create_table('approvals', sa.Column('id', sa.String(), primary_key=True), sa.Column('project_id', sa.String(), sa.ForeignKey('projects.id'), nullable=False), sa.Column('task_id', sa.String(), sa.ForeignKey('tasks.id'), nullable=False), sa.Column('requester', sa.String(), nullable=False), sa.Column('operation', sa.String(), nullable=False), sa.Column('target', sa.String(), nullable=False), sa.Column('action', sa.Text(), nullable=False), sa.Column('artifact', sa.String(), nullable=False), sa.Column('status', sa.String(), nullable=False), sa.Column('requested_at', sa.String(), nullable=False), sa.Column('expires_at', sa.String(), nullable=False), sa.Column('resolved_at', sa.String()), sa.Column('operator', sa.String()), sa.Column('resolution_note', sa.String()), sa.Column('consumed_at', sa.String()), sa.Column('consumed_by', sa.String()), sa.Column('version', sa.Integer(), nullable=False))
    for table, columns in {'incidents': ['project_id'], 'adapter_health': ['project_id'], 'approvals': ['project_id', 'task_id']}.items():
        for column in columns:
            op.create_index(f'ix_{table}_{column}', table, [column])


def downgrade():
    for table in ['approvals', 'adapter_health', 'incidents']:
        op.drop_table(table)
