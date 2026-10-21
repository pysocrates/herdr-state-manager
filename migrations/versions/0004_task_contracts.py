"""Immutable contract revisions and lifetime delegation authorizations."""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tasks', sa.Column('resource_labels', sa.JSON(), nullable=False, server_default='[]'))
    op.create_index('ix_tasks_parent_id', 'tasks', ['parent_id'])
    op.create_table('task_contracts',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('task_id', sa.String(), sa.ForeignKey('tasks.id'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('expected_task_version', sa.Integer(), nullable=False),
        sa.Column('expected_contract_version', sa.Integer(), nullable=False),
        sa.Column('specification', sa.JSON(), nullable=False),
        *[sa.Column(k, sa.String(), nullable=False) for k in
          ('actor', 'reason', 'created_at', 'authorization_started_at')],
        sa.UniqueConstraint('task_id', 'revision'))
    op.create_index('ix_task_contracts_task_id', 'task_contracts', ['task_id'])
    op.create_table('delegations',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('task_id', sa.String(), sa.ForeignKey('tasks.id'), nullable=False),
        sa.Column('parent_id', sa.String(), sa.ForeignKey('tasks.id'), nullable=False),
        sa.Column('parent_contract_id', sa.String(), sa.ForeignKey('task_contracts.id')),
        sa.Column('governing_contract_ids', sa.JSON(), nullable=False),
        sa.Column('authorized_by', sa.String(), nullable=False),
        sa.Column('spawn_reason', sa.String()),
        sa.Column('created_at', sa.String(), nullable=False))
    for key in ('task_id', 'parent_id'):
        op.create_index('ix_delegations_' + key, 'delegations', [key])
    # Only surviving pre-migration relationships are knowable; never invent an actor.
    op.execute("INSERT INTO delegations (id, task_id, parent_id, governing_contract_ids, authorized_by, created_at) SELECT id, id, parent_id, '[]', 'migration:unknown', created_at FROM tasks WHERE parent_id IS NOT NULL")


def downgrade():
    op.drop_table('delegations')
    op.drop_table('task_contracts')
    op.drop_index('ix_tasks_parent_id', 'tasks')
    op.drop_column('tasks', 'resource_labels')
