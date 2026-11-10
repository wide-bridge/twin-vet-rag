"""dog birth date (known as far as the guardian knows it) and age basis

Revision ID: 7c2a9e1f4b10
Revises: 1db3cb2e380e
Create Date: 2026-11-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7c2a9e1f4b10"
down_revision: Union[str, Sequence[str], None] = "1db3cb2e380e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("consultation", sa.Column("dog_birth_date", sa.Date(), nullable=True))
    # existing rows were entered as an age: they count as estimates
    op.add_column("consultation", sa.Column("dog_age_basis", sa.String(length=8), nullable=False,
                                            server_default="estimate"))
    op.create_check_constraint("ck_consultation_age_basis", "consultation",
                               "dog_age_basis IN ('day','month','year','estimate')")
    op.create_check_constraint("ck_consultation_birth_date", "consultation",
                               "(dog_age_basis = 'estimate') = (dog_birth_date IS NULL)")


def downgrade() -> None:
    op.drop_constraint("ck_consultation_birth_date", "consultation", type_="check")
    op.drop_constraint("ck_consultation_age_basis", "consultation", type_="check")
    op.drop_column("consultation", "dog_age_basis")
    op.drop_column("consultation", "dog_birth_date")
