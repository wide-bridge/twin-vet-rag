"""m03 vet review of the AI answer (HITL 우리)

Revision ID: a7f3c9d2e481
Revises: c41a590ed142
Create Date: 2026-11-16 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a7f3c9d2e481'
down_revision: Union[str, Sequence[str], None] = 'c41a590ed142'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'vet_ai_review',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('question_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('vet_question.id', ondelete='CASCADE'), nullable=False),
        sa.Column('verdict', sa.String(12), nullable=False),
        sa.Column('safety_issue', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('corrected_answer', sa.Text(), nullable=True),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("verdict IN ('correct', 'needs_fix', 'different')", name='ck_vet_ai_review_verdict'),
    )
    op.create_index('ix_vet_ai_review_question_id', 'vet_ai_review', ['question_id'])
    op.add_column('review_item', sa.Column('vet_ai_review_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_review_item_vet_ai_review', 'review_item', 'vet_ai_review', ['vet_ai_review_id'], ['id'],
                          ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_review_item_vet_ai_review', 'review_item', type_='foreignkey')
    op.drop_column('review_item', 'vet_ai_review_id')
    op.drop_index('ix_vet_ai_review_question_id', table_name='vet_ai_review')
    op.drop_table('vet_ai_review')
