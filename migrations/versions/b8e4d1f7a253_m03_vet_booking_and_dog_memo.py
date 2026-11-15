"""m03 vet booking info and the vet's own dog memo

Revision ID: b8e4d1f7a253
Revises: a7f3c9d2e481
Create Date: 2026-11-16 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8e4d1f7a253'
down_revision: Union[str, Sequence[str], None] = 'a7f3c9d2e481'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('vet_account', sa.Column('booking_phone', sa.String(40), nullable=True))
    op.add_column('vet_account', sa.Column('booking_hours', sa.String(300), nullable=True))
    op.add_column('vet_account', sa.Column('booking_note', sa.String(500), nullable=True))
    op.create_table(
        'vet_dog_memo',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('profile_id', sa.UUID(), nullable=False),
        sa.Column('vet_id', sa.UUID(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['profile_id'], ['dog_profile.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['vet_id'], ['vet_account.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('profile_id', 'vet_id', name='uq_vet_dog_memo'),
    )
    op.create_index('ix_vet_dog_memo_profile_id', 'vet_dog_memo', ['profile_id'])
    op.create_index('ix_vet_dog_memo_vet_id', 'vet_dog_memo', ['vet_id'])


def downgrade() -> None:
    op.drop_index('ix_vet_dog_memo_vet_id', table_name='vet_dog_memo')
    op.drop_index('ix_vet_dog_memo_profile_id', table_name='vet_dog_memo')
    op.drop_table('vet_dog_memo')
    op.drop_column('vet_account', 'booking_note')
    op.drop_column('vet_account', 'booking_hours')
    op.drop_column('vet_account', 'booking_phone')
