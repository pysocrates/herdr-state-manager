"""Initial durable state schema (frozen migration, not runtime metadata)."""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('projects', sa.Column('id', sa.String(), primary_key=True), sa.Column('name', sa.String(), nullable=False), sa.Column('configuration', sa.JSON(), nullable=False), sa.Column('enabled', sa.Boolean(), nullable=False), sa.Column('created_at', sa.String(), nullable=False))
    op.create_table('agent_sessions', sa.Column('id', sa.String(), primary_key=True), sa.Column('project_id', sa.String(), sa.ForeignKey('projects.id'), nullable=False), sa.Column('profile', sa.String(), nullable=False), sa.Column('external_session_id', sa.String()), sa.Column('started_at', sa.String(), nullable=False), sa.Column('ended_at', sa.String()), sa.Column('last_observed_at', sa.String()), sa.Column('heartbeat_at', sa.String()), sa.Column('runtime_status', sa.String(), nullable=False), sa.Column('telemetry', sa.JSON(), nullable=False))
    op.create_table('tasks', sa.Column('id', sa.String(), primary_key=True), sa.Column('project_id', sa.String(), sa.ForeignKey('projects.id'), nullable=False), sa.Column('task_key', sa.String(), nullable=False), sa.Column('title', sa.String(), nullable=False), sa.Column('objective', sa.Text(), nullable=False), sa.Column('state', sa.String(), nullable=False), sa.Column('parent_id', sa.String(), sa.ForeignKey('tasks.id')), sa.Column('owner_session_id', sa.String(), sa.ForeignKey('agent_sessions.id')), sa.Column('repository_path', sa.String()), sa.Column('worktree_path', sa.String()), sa.Column('base_commit', sa.String()), sa.Column('created_at', sa.String(), nullable=False), sa.Column('updated_at', sa.String(), nullable=False), sa.Column('completed_at', sa.String()), sa.Column('version', sa.Integer(), nullable=False), sa.UniqueConstraint('project_id', 'task_key'))
    op.create_table('panes', sa.Column('id', sa.String(), primary_key=True), sa.Column('project_id', sa.String(), sa.ForeignKey('projects.id'), nullable=False), sa.Column('provider', sa.String(), nullable=False), sa.Column('external_pane_id', sa.String(), nullable=False), sa.Column('task_id', sa.String(), sa.ForeignKey('tasks.id')), sa.Column('agent_session_id', sa.String(), sa.ForeignKey('agent_sessions.id')), sa.Column('runtime_status', sa.String(), nullable=False), sa.Column('created_at', sa.String(), nullable=False), sa.Column('terminated_at', sa.String()), sa.Column('last_observed_at', sa.String()), sa.Column('heartbeat_at', sa.String()), sa.UniqueConstraint('project_id', 'provider', 'external_pane_id'))
    op.create_table('events', sa.Column('cursor', sa.Integer(), primary_key=True, autoincrement=True), sa.Column('id', sa.String(), unique=True, nullable=False), sa.Column('project_id', sa.String(), sa.ForeignKey('projects.id'), nullable=False), sa.Column('task_id', sa.String(), sa.ForeignKey('tasks.id')), sa.Column('timestamp', sa.String(), nullable=False), sa.Column('event_type', sa.String(), nullable=False), sa.Column('actor', sa.String(), nullable=False), sa.Column('previous_state', sa.String()), sa.Column('new_state', sa.String()), sa.Column('details', sa.JSON(), nullable=False), sqlite_autoincrement=True)
    op.create_table('idempotency', sa.Column('scope', sa.String(), primary_key=True), sa.Column('digest', sa.String(), nullable=False), sa.Column('expires_at', sa.String(), nullable=False), sa.Column('response', sa.JSON(), nullable=False), sa.Column('status', sa.Integer(), nullable=False))
    for table, columns in {'agent_sessions': ['project_id'], 'tasks': ['project_id', 'state'], 'panes': ['project_id'], 'events': ['project_id', 'task_id']}.items():
        for column in columns:
            op.create_index(f'ix_{table}_{column}', table, [column])


def downgrade():
    for table in ['idempotency', 'events', 'panes', 'tasks', 'agent_sessions', 'projects']:
        op.drop_table(table)
