"""add candidate_invalidation table (G6A-05/OI-PF-204)

Revision ID: g6a06_candidate_invalidation
Revises: g4_publish_tables
Create Date: 2026-08-15 08:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g6a06_candidate_invalidation'
down_revision: Union[str, Sequence[str], None] = 'g4_publish_tables'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('candidate_invalidation',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('schema_version', sa.String(length=16), nullable=False),
    sa.Column('old_candidate_id', sa.String(length=64), nullable=False),
    sa.Column('new_candidate_id', sa.String(length=64), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('invalidated_at', sa.DateTime(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('old_candidate_id', name='uq_candidate_invalidation_old')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('candidate_invalidation')
