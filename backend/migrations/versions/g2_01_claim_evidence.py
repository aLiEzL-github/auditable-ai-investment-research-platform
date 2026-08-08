"""G2-01: claim / evidence_record / claim_evidence_link

Revision ID: g2_01_claim_evidence
Revises: 81a2fd86971d
Create Date: 2026-08-08 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'g2_01_claim_evidence'
down_revision: Union[str, Sequence[str], None] = '81a2fd86971d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('claim',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('schema_version', sa.String(length=16), nullable=False),
        sa.Column('statement', sa.Text(), nullable=False),
        sa.Column('category', sa.String(length=64), nullable=False),
        sa.Column('materiality', sa.String(length=16), nullable=False),
        sa.Column('refs', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_table('evidence_record',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('schema_version', sa.String(length=16), nullable=False),
        sa.Column('artifact_id', sa.String(length=64), nullable=False),
        sa.Column('snapshot_id', sa.String(length=64), nullable=False),
        sa.Column('schema_ver', sa.String(length=32), nullable=False),
        sa.Column('parser_version', sa.String(length=32), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('sha256', name='uq_evidence_sha256'),
        sa.ForeignKeyConstraint(['artifact_id'], ['raw_artifact.id'])
    )
    op.create_table('rights_decision',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('schema_version', sa.String(length=16), nullable=False),
        sa.Column('source_id', sa.String(length=64), nullable=False),
        sa.Column('action', sa.String(length=16), nullable=False),
        sa.Column('scope', sa.String(length=512), nullable=False),
        sa.Column('policy_version', sa.String(length=32), nullable=False),
        sa.Column('verdict', sa.String(length=16), nullable=False),
        sa.Column('decided_at', sa.DateTime(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['source_id'], ['source.id'])
    )
    op.create_table('claim_evidence_link',
        sa.Column('claim_id', sa.String(length=64), nullable=False),
        sa.Column('evidence_id', sa.String(length=64), nullable=False),
        sa.Column('schema_version', sa.String(length=16), nullable=False),
        sa.Column('direction', sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint('claim_id', 'evidence_id'),
        sa.ForeignKeyConstraint(['claim_id'], ['claim.id']),
        sa.ForeignKeyConstraint(['evidence_id'], ['evidence_record.id'])
    )


def downgrade() -> None:
    op.drop_table('claim_evidence_link')
    op.drop_table('rights_decision')
    op.drop_table('evidence_record')
    op.drop_table('claim')
