"""Durable artifact evidence, checkpoints and incident remediation ledger."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def col(name, kind=sa.String, nullable=False, **kw):
    return sa.Column(name, kind(), nullable=nullable, **kw)


def ref(name, target, nullable=False, **kw):
    return sa.Column(name, sa.String(), sa.ForeignKey(target + '.id'), nullable=nullable, **kw)


def common():
    return [col('id', primary_key=True), ref('project_id', 'projects'), ref('task_id', 'tasks')]


def upgrade():
    # SQLite supports an inline nullable REFERENCES column without rebuilding
    # the populated incidents table; Alembic's add_column splits the constraint.
    op.execute('ALTER TABLE incidents ADD COLUMN task_id VARCHAR REFERENCES tasks(id)')
    for name in ('failure_kind', 'description', 'resolution_note', 'evidence_reference', 'proposed_action', 'scope_impact'):
        op.add_column('incidents', col(name, nullable=True))
    op.add_column('incidents', col('version', sa.Integer, server_default='1'))
    op.create_index('ix_incidents_task_id', 'incidents', ['task_id'])
    op.create_table('deliverables', *common(), col('key'), col('title'), col('version', sa.Integer),
        ref('superseded_by', 'deliverables', nullable=True), col('created_at'), sa.UniqueConstraint('task_id', 'key'))
    op.create_table('deliverable_revisions', *common(), ref('deliverable_id', 'deliverables'),
        col('revision', sa.Integer), col('reference'), col('artifact_identity'), col('actor'), col('created_at'),
        sa.UniqueConstraint('deliverable_id', 'revision'))
    op.create_table('validation_evidence', *common(), ref('revision_id', 'deliverable_revisions'),
        col('artifact_identity'), col('outcome'), col('reference'), col('actor'), col('created_at'))
    op.create_table('checkpoints', *common(), col('task_version', sa.Integer), col('contract_ids', sa.JSON),
        col('completed_requirements', sa.JSON), col('blockers', sa.JSON), col('next_authorized_action', nullable=True),
        col('actor'), col('created_at'))
    op.create_table('checkpoint_revisions', ref('checkpoint_id', 'checkpoints', primary_key=True),
        ref('revision_id', 'deliverable_revisions', primary_key=True), ref('evidence_id', 'validation_evidence'))
    op.create_table('remediations', *common(), ref('incident_id', 'incidents'),
        ref('approval_id', 'approvals', unique=True), col('canonical_identity'), col('contract_ids', sa.JSON),
        col('budget_task_ids', sa.JSON), col('status'), col('version', sa.Integer), col('actor'),
        col('created_at'), col('authorized_at', nullable=True))
    for table, fields in {
        'deliverables': ['project_id', 'task_id'],
        'deliverable_revisions': ['project_id', 'task_id', 'deliverable_id'],
        'validation_evidence': ['project_id', 'task_id', 'revision_id'],
        'checkpoints': ['project_id', 'task_id'],
        'remediations': ['project_id', 'task_id', 'incident_id', 'canonical_identity'],
    }.items():
        for field in fields:
            op.create_index(f'ix_{table}_{field}', table, [field])


def downgrade():
    for table in ('remediations', 'checkpoint_revisions', 'checkpoints', 'validation_evidence', 'deliverable_revisions', 'deliverables'):
        op.drop_table(table)
    op.drop_index('ix_incidents_task_id', 'incidents')
    for name in ('task_id', 'failure_kind', 'description', 'resolution_note', 'evidence_reference', 'proposed_action', 'scope_impact', 'version'):
        op.drop_column('incidents', name)
