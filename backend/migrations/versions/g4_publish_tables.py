"""add G4 publish tables: approval / release / current_pointer

Revision ID: g4_publish_tables
Revises: 81a2fd86971d
Create Date: 2026-08-11 06:40:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g4_publish_tables'
down_revision: Union[str, Sequence[str], None] = 'g2_01_claim_evidence'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('approval',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('schema_version', sa.String(length=16), nullable=False),
    sa.Column('object_ref', sa.String(length=64), nullable=False),
    sa.Column('approver', sa.String(length=64), nullable=False),
    sa.Column('approved_at', sa.DateTime(), nullable=False),
    sa.Column('subject_root_hash', sa.String(length=64), nullable=False),
    sa.Column('workflow', sa.String(length=64), nullable=False),
    sa.Column('scope_id', sa.String(length=64), nullable=False),
    sa.Column('current_key', sa.String(length=64), nullable=False),
    sa.Column('inputs_hash', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('token', sa.String(length=16), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('release',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('schema_version', sa.String(length=16), nullable=False),
    sa.Column('workflow', sa.String(length=64), nullable=False),
    sa.Column('scope_id', sa.String(length=64), nullable=False),
    sa.Column('current_key', sa.String(length=64), nullable=False),
    sa.Column('version', sa.String(length=32), nullable=False),
    sa.Column('parent_cas', sa.String(length=64), nullable=True),
    sa.Column('subject_root_hash', sa.String(length=64), nullable=False),
    sa.Column('manifest_hash', sa.String(length=64), nullable=False),
    sa.Column('approval_id', sa.String(length=64), nullable=False),
    sa.Column('released_at', sa.DateTime(), nullable=False),
    sa.Column('version_cas', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['approval_id'], ['approval.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workflow', 'scope_id', 'current_key', 'version',
                        name='uq_release_domain_version')
    )
    op.create_table('current_pointer',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('schema_version', sa.String(length=16), nullable=False),
    sa.Column('workflow', sa.String(length=64), nullable=False),
    sa.Column('scope_id', sa.String(length=64), nullable=False),
    sa.Column('current_key', sa.String(length=64), nullable=False),
    sa.Column('release_id', sa.String(length=64), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('changed_by', sa.String(length=64), nullable=False),
    sa.Column('changed_at', sa.DateTime(), nullable=False),
    sa.Column('approval_id', sa.String(length=64), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['approval_id'], ['approval.id'], ),
    sa.ForeignKeyConstraint(['release_id'], ['release.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('workflow', 'scope_id', 'current_key', 'seq',
                        name='uq_pointer_domain_seq')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('current_pointer')
    op.drop_table('release')
    op.drop_table('approval')
